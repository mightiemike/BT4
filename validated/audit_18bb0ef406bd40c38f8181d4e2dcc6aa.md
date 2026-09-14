I have enough evidence now to conclude the analysis.

The key finding: `action_delete_account` (`runtime/runtime/src/actions.rs:330-406`) generates a `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)` for the deleted account's remaining balance. This refund is a system-predecessor receipt (`is_refund = true`, per `runtime/runtime/src/lib.rs:596`), and `implicit_creation_allowed` explicitly returns `false` for any account type when `is_refund` is true (`runtime/runtime/src/actions.rs:930-933`, confirmed by the test `refund_may_not_create_universal_account` at `runtime/runtime/src/tests/apply.rs:6883-6926`). Meanwhile, `validate_delete_action` (`runtime/runtime/src/action_validation.rs:447-451`) only checks that `beneficiary_id` is a *syntactically* valid `AccountId` — it never checks that the beneficiary account actually exists. Per `docs/RuntimeSpec/Refunds.md:12`: "If the execution of a refund fails, the refund amount is burnt." This is confirmed at `runtime/runtime/src/lib.rs:1047-1054`, which burns the full deposit into `stats.balance.other_burnt_amount` whenever a system-predecessor (refund) receipt's action fails.

### Title
Permanent fund loss via unchecked `beneficiary_id` in `DeleteAccountAction` sent to a non-existent named account - (File: `runtime/runtime/src/actions.rs`)

### Summary
`action_delete_account` unconditionally forwards the deleted account's entire balance to `delete_account.beneficiary_id` as a system refund receipt, without ever checking that the beneficiary account exists. Because refund receipts can never implicitly create an account, sending the refund to a currently-nonexistent named account causes the transfer to fail, and the whole balance is irrecoverably burnt.

### Finding Description
`validate_delete_action` [1](#0-0)  only calls `validate_action_account_id`, which enforces NEAR account-id syntax rules; it performs no existence check on `beneficiary_id`.

`action_delete_account` then unconditionally builds a refund receipt for the account's entire balance: [2](#0-1) .

That receipt is executed with `predecessor_id() == "system"`, marking it as a refund (`is_refund = true`) [3](#0-2) . `implicit_creation_allowed` unconditionally rejects account creation for *any* account type when the receipt is a refund [4](#0-3) , so if `beneficiary_id` names an account that does not currently exist (e.g. a typo, a since-deleted account, or an account the caller mistakenly believes exists), `check_account_existence` returns `ActionErrorKind::AccountDoesNotExist` for the `Transfer` action inside that refund receipt.

Per the documented refund semantics, "If the execution of a refund fails, the refund amount is burnt" [5](#0-4) , which is exactly what happens: `apply_action` returns the `AccountDoesNotExist` failure inside the refund receipt, and the runtime adds the receipt's whole deposit to `stats.balance.other_burnt_amount` [6](#0-5) . This is precisely analogous to the reported `recoverFunds`/zero-address bug class: a caller-controlled destination parameter that is syntactically validated but never checked for reachability, causing attached value to be permanently destroyed once the top-level (only) action of the receipt fails.

### Impact Explanation
Any account holder who submits `DeleteAccountAction { beneficiary_id }` with a beneficiary that does not exist at execution time — whether by typo, race (the intended beneficiary account is deleted between transaction construction and execution), or simple user error — permanently and irrecoverably burns the entire remaining balance of the deleted account. There is no recovery path: the receipt fails, funds are added to `other_burnt_amount`, and the account is already removed. For accounts holding a non-trivial NEAR balance, this is a direct, unrecoverable loss of user funds, matching a Medium severity impact per the referenced bug class (permanently frozen/burned funds).

### Likelihood Explanation
`beneficiary_id` is a free-form parameter fully controlled by the transaction signer or by any contract issuing `promise_batch_action_delete_account` on the signer's behalf (e.g. `runtime/near-vm-runner/src/wasmtime_runner/logic.rs:4038-4072`). No additional privilege is required — any ordinary account owner reachable via a single signed transaction can trigger this by naming a beneficiary that turns out not to exist, and it is easy to get wrong (e.g., relayer/contract-supplied beneficiary strings, or beneficiaries expected to be created in the same block but not yet committed).

### Recommendation
Before generating the balance-refund receipt in `action_delete_account`, verify that `beneficiary_id` refers to an existing account (or restrict `DeleteAccountAction` to only take effect once the beneficiary is confirmed to exist), and reject the action with a clear validation/execution error (rather than silently burning funds) if it does not. Alternatively, allow the beneficiary refund receipt to fall back to implicit account creation rules consistent with ordinary transfers when the beneficiary is a valid implicit/eth-implicit id, and otherwise require the beneficiary to be pre-existing at action-validation time.

### Proof of Concept
1. Create account `alice.near` with balance `B` (non-zero) and no staking lock.
2. Submit a transaction with a single `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "nonexistent.near".parse().unwrap() })` where `nonexistent.near` is a syntactically valid but currently non-existent named account (this passes `validate_delete_action`, `runtime/runtime/src/action_validation.rs:447-451`, since only syntax is checked).
3. `action_delete_account` removes `alice.near` and enqueues `Receipt::new_balance_refund(&"nonexistent.near", B)` (`runtime/runtime/src/actions.rs:380-386`).
4. When this system refund receipt is applied, `check_account_existence` for its `Transfer` action returns `AccountDoesNotExist` because `implicit_creation_allowed` returns `false` for a refund (`runtime/runtime/src/actions.rs:930-933`), matching the existing test `refund_may_not_create_universal_account` (`runtime/runtime/src/tests/apply.rs:6870-6926`), which already demonstrates this exact failure path for a `0u` account and asserts the account is not created afterward.
5. Per `runtime/runtime/src/lib.rs:1047-1054`, the failed refund's full deposit `B` is added to `stats.balance.other_burnt_amount` — the funds are permanently destroyed, with no recovery receipt generated (refunds do not themselves get refunded).

### Citations

**File:** runtime/runtime/src/action_validation.rs (L447-451)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
```

**File:** runtime/runtime/src/actions.rs (L380-386)
```rust
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
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

**File:** runtime/runtime/src/lib.rs (L595-597)
```rust
        let account_id = receipt.receiver_id();
        let is_refund = receipt.predecessor_id().is_system();
        let receipt_shape = ReceiptShape { is_refund, is_the_only_action: actions.len() == 1 };
```

**File:** runtime/runtime/src/lib.rs (L1047-1054)
```rust
        let gas_refund_result = if receipt.predecessor_id().is_system() {
            // If the refund fails tokens are burned.
            if result.result.is_err() {
                stats.balance.other_burnt_amount = safe_add_balance(
                    stats.balance.other_burnt_amount,
                    total_deposit(&action_receipt.actions())?,
                )?
            }
```

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```
