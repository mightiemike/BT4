### Title
`DeleteAccount` unconditionally sends the beneficiary refund without checking the beneficiary exists, permanently burning the account's balance - ([File: runtime/runtime/src/actions.rs])

### Summary
`action_delete_account` deletes the account and unconditionally emits a balance-refund receipt to `delete_account.beneficiary_id` for the account's full balance, without ever checking that the beneficiary account exists. Since refund receipts are never allowed to implicitly create an account (`is_refund` always forces `implicit_creation_allowed` to `false`), a `DeleteAccount` naming a nonexistent (or since-deleted) beneficiary causes the transferred balance to fail delivery and be permanently burned, while the account deletion itself has already been irreversibly committed.

### Finding Description
`action_delete_account` (`runtime/runtime/src/actions.rs:330-406`) performs the deletion unconditionally: [1](#0-0) 
```
// We use current amount as a pay out to beneficiary.
let account_balance = account_ref.amount();
if account_balance > Balance::ZERO {
    result
        .new_receipts
        .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
}
let remove_result = remove_account(state_update, account_id)?;
```
There is no lookup of the beneficiary account and no validation that it exists before the account is removed. The only precondition enforced before reaching this code is in `check_actor_permissions` for `Action::DeleteAccount`, which checks actor identity and zero locked stake — it does not check the beneficiary at all: [2](#0-1) 

The `Receipt::new_balance_refund` produced here is a system/refund receipt. `check_account_existence` explicitly forbids a refund from implicitly creating any account type: [3](#0-2) 
```
fn implicit_creation_allowed(account_type: AccountType, receipt_shape: ReceiptShape) -> bool {
    let ReceiptShape { is_refund, is_the_only_action } = receipt_shape;
    if is_refund {
        return false; // Refund can never create an account
    }
    ...
```
So if `beneficiary_id` does not exist (never created, or itself deleted/never funded), the balance-refund receipt fails with `AccountDoesNotExist`. Per the documented invariant, a failed refund receipt does not retry or redirect funds anywhere recoverable — it is burned: [4](#0-3)  "Refund receipts are free: system-predecessor receipts burn zero gas; a failed refund burns its deposit into `other_burnt_amount` rather than refunding."

By the time this refund receipt is processed, the `DeleteAccount` action that removed the source account (with `*account = None`) has already committed successfully in the same receipt — deletion and refund-emission are not conditioned on beneficiary existence, and the two are not atomic across the follow-on refund receipt's execution. The test suite's own comment on a beneficiary-existing test case documents the failure mode directly: [5](#0-4)  "The beneficiary has to exist, otherwise the balance transfer the delete sends it would come straight back as a refund" (i.e., burnt, since a refund cannot create the beneficiary).

This is the direct analog of the Y2K `triggerEndEpoch` bug: a function that transfers value to another party should first verify the precondition that makes the transfer meaningful/recoverable (there, that a vault has funds so the transfer is legitimate; here, that the beneficiary account actually exists so the transfer can be delivered), but the check is missing, and the value-moving action proceeds anyway, unconditionally and irreversibly.

### Impact Explanation
Any account holder can trigger permanent, unrecoverable loss of their own account balance by submitting a single `DeleteAccount` action naming a nonexistent `beneficiary_id` (e.g., a typo'd account, an account that was never created, or one that gets deleted in the interim). The funds are not returned to the signer, not credited to any validator/treasury account through normal accounting, and not recoverable — they are absorbed into `other_burnt_amount`, silently reducing total supply outside the documented issuance/burn accounting paths that assume burns come from gas fees. This is an unconditional, one-transaction fund-loss bug reachable by any ordinary (non-privileged) account owner deleting their own account, matching the "loss of funds" impact class from the reference report (permanently frozen/burned funds rather than delivered to the intended party).

### Likelihood Explanation
High likelihood of accidental occurrence (a very common real-world action: typo in beneficiary account, or targeting a since-deleted/never-created sub-account), and trivially triggerable by any account holder. No special privileges, timing races, or validator/attacker cooperation are required — a single signed transaction with a `DeleteAccount` action referencing a non-existent `beneficiary_id` is sufficient.

### Recommendation
Before removing the account and queuing the balance-refund receipt in `action_delete_account`, verify that `delete_account.beneficiary_id` refers to an existing (and initialized) account, mirroring the null/precondition check the Y2K fix added to `triggerEndEpoch`. If the beneficiary does not exist, the action should fail with an explicit `ActionError` (e.g., `BeneficiaryDoesNotExist`) rather than silently proceeding to delete the account and burn the funds through a doomed refund receipt.

### Proof of Concept
1. Create account `alice.near` with a positive balance and no locked stake.
2. Sign a `DeleteAccount { beneficiary_id: "nonexistent.near" }` action as `alice.near` (actor == account, satisfying `check_actor_permissions`).
3. Runtime executes `action_delete_account`: pushes `Receipt::new_balance_refund("nonexistent.near", account_balance)`, then calls `remove_account`, deleting `alice.near` from state.
4. The refund receipt is later processed; `check_account_existence` for the implicit `Transfer` inside the refund receipt rejects account creation because `is_refund == true` (`implicit_creation_allowed` returns `false`), producing `ActionErrorKind::AccountDoesNotExist`.
5. Per `runtime/runtime/src/lib.rs:929`, the failed refund's deposit is folded into `other_burnt_amount` instead of being returned to `alice.near` or delivered to anyone — the funds are permanently destroyed, while `alice.near`'s account is already gone.

### Citations

**File:** runtime/runtime/src/actions.rs (L380-387)
```rust
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
    let remove_result = remove_account(state_update, account_id)?;
```

**File:** runtime/runtime/src/actions.rs (L777-792)
```rust
        Action::DeleteAccount(_) => {
            if actor_id != account_id {
                return Err(ActionErrorKind::ActorNoPermission {
                    account_id: account_id.clone(),
                    actor_id: actor_id.clone(),
                }
                .into());
            }
            let account = account.as_ref().unwrap();
            if !account.locked().is_zero() {
                return Err(ActionErrorKind::DeleteAccountStaking {
                    account_id: account_id.clone(),
                }
                .into());
            }
        }
```

**File:** runtime/runtime/src/actions.rs (L928-933)
```rust
/// Whether a transfer to an account that does not exist yet may create it.
fn implicit_creation_allowed(account_type: AccountType, receipt_shape: ReceiptShape) -> bool {
    let ReceiptShape { is_refund, is_the_only_action } = receipt_shape;
    if is_refund {
        return false; // Refund can never create an account
    }
```

**File:** protocol-model/spec/runtime-execution.md (L151-153)
```markdown
- **Invalid txs make progress, not failure**: a chunk with invalid transactions is not rejected; the offending txs are skipped during conversion, polluting the chain with junk but keeping the shard live (`runtime/runtime/src/lib.rs:1706` doc; skip sites at `:1994`, `:2199`).
- **Refund receipts are free**: system-predecessor receipts burn zero gas; a failed refund burns its deposit into `other_burnt_amount` rather than refunding (`runtime/runtime/src/lib.rs:929`, `:972`).
- **Delayed receipts must stay valid**: a delayed receipt that fails `validate_receipt` on dequeue is treated as `StorageInconsistentState` (`runtime/runtime/src/lib.rs:2500`).
```

**File:** runtime/runtime/src/tests/apply.rs (L6354-6357)
```rust

        // The beneficiary has to exist, otherwise the balance transfer the delete
        // sends it would come straight back as a refund.
        let beneficiary: AccountId = "beneficiary.near".parse().unwrap();
```
