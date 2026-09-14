### Title
DeleteAccount to a Non-Existent Beneficiary Permanently Burns the Account's Balance Instead of Refunding It - (File: `runtime/runtime/src/actions.rs`)

### Summary
`DeleteAccountAction::beneficiary_id` is only validated for *syntactic* correctness, not for whether the account actually exists. When a user deletes their own account and specifies a beneficiary that does not exist (a typo, a not-yet-created sub-account, or any other unregistered but well-formed `AccountId`), the resulting balance-transfer receipt fails and the account's entire remaining NEAR balance is permanently burned rather than returned to the user or to any recoverable party.

### Finding Description
`validate_delete_action` only calls `validate_action_account_id`, which checks that `beneficiary_id` is a syntactically valid `AccountId` string — it never checks that the account exists on chain: [1](#0-0) 

When the `DeleteAccount` action executes, the account's remaining balance is packaged into a `Receipt::new_balance_refund` addressed to `beneficiary_id`, and the account is removed: [2](#0-1) 

`Receipt::new_balance_refund` sets `predecessor_id` to `"system"`, marking it as a refund receipt: [3](#0-2) 

When this refund receipt is later processed, `check_account_existence` for a `Transfer` action rejects it with `AccountDoesNotExist` if the beneficiary account is absent, because `is_refund` disables implicit account creation: [4](#0-3) [5](#0-4) 

Critically, when a system/refund receipt fails, the runtime does **not** generate a further refund — it unconditionally burns the deposit into `other_burnt_amount`: [6](#0-5) 

This burn-on-failed-refund behavior is documented as the expected (if dangerous) design: [7](#0-6) 

The project's own tests acknowledge this precondition but do not enforce it protocol-side — the comment on the `delete_after_init_removes_account` test explicitly notes the beneficiary "has to exist, otherwise the balance transfer the delete sends it would come straight back as a refund" (in fact it is burned, not refunded): [8](#0-7) 

This is the direct nearcore analog of the reported `mintForToken()` issue: a caller-supplied destination account id is accepted without existence validation, and the resulting fund transfer is unrecoverable — a self-inflicted but unrecoverable loss triggered purely by the absence of an existence check at validation time, exactly as `to != address(0)` would have prevented the original bug.

### Impact Explanation
Any unprivileged account holder who submits a `DeleteAccount` action with a `beneficiary_id` that is syntactically valid but does not exist (e.g., a mistyped account name, an account that hasn't been created yet, or one that was deleted between the transaction being signed and executed) will have their entire account balance permanently and irrecoverably burned rather than refunded to any party. This is a "permanently frozen funds" outcome reachable by a single ordinary transaction from any signer, with no privileged role required.

### Likelihood Explanation
Likelihood is non-trivial: `beneficiary_id` is fully attacker/user controlled and only checked for string-format validity at admission time (`validate_delete_action` / `validate_action_account_id`), never for existence. Any mistake, race (beneficiary deleted between signing and inclusion), or malicious relayer/dApp front-end that supplies a bogus-but-valid beneficiary id to an unsuspecting user causes silent, permanent fund loss with no recovery path, and no error surfaces the true cause distinctly from an ordinary `AccountDoesNotExist` refund failure.

### Recommendation
Add an existence check for `beneficiary_id` before executing `DeleteAccount`, or reject/refund the delete itself if the beneficiary does not exist at execution time (rather than allowing the balance-refund receipt to be produced and then silently burned by the generic "failed refund is burned" path). At minimum, this failure mode should be distinguished from ordinary refund failures so validators and RPC callers can flag it, and wallets/SDKs should be required to verify beneficiary existence before submitting the transaction.

### Proof of Concept
1. Account `alice.near` holds a balance and calls `DeleteAccount { beneficiary_id: "typo-beneficiary.near" }` where `typo-beneficiary.near` does not exist on chain.
2. `validate_delete_action` accepts the action because `typo-beneficiary.near` is a syntactically valid `AccountId`.
3. `action_delete_account` removes `alice.near` and enqueues `Receipt::new_balance_refund("typo-beneficiary.near", alice_balance)` with `predecessor_id = "system"`.
4. When this receipt executes, `check_account_existence` rejects the `Transfer` action with `ActionErrorKind::AccountDoesNotExist` because the receipt is a refund (`is_refund = true` disables implicit creation).
5. Because `receipt.predecessor_id().is_system()` is true and `result.result.is_err()`, the runtime adds `total_deposit(...)` (i.e., `alice_balance`) to `stats.balance.other_burnt_amount` instead of generating any further refund.
6. `alice.near`'s entire balance is permanently burned with no path to recovery, matching the `delete_after_init_removes_account` test's own caveat that the beneficiary "has to exist" for the funds not to be lost.

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

**File:** runtime/runtime/src/actions.rs (L842-850)
```rust
        Action::Transfer(_) => {
            let account_type = get_account_type(account_id, config);
            if account.is_none() && !implicit_creation_allowed(account_type, receipt_shape) {
                return Err(ActionErrorKind::AccountDoesNotExist {
                    account_id: account_id.clone(),
                }
                .into());
            }
        }
```

**File:** runtime/runtime/src/actions.rs (L928-947)
```rust
/// Whether a transfer to an account that does not exist yet may create it.
fn implicit_creation_allowed(account_type: AccountType, receipt_shape: ReceiptShape) -> bool {
    let ReceiptShape { is_refund, is_the_only_action } = receipt_shape;
    if is_refund {
        return false; // Refund can never create an account
    }

    match account_type {
        // Named accounts can never be implicitly created by transfer
        AccountType::NamedAccount => false,
        // Near-implicit, Eth-implicit, and deterministic accounts can only be created
        // if transfer is the only action, to avoid account hijacking.
        AccountType::NearImplicitAccount
        | AccountType::EthImplicitAccount
        | AccountType::NearDeterministicAccount => is_the_only_action,
        // Universal account creation does NOT require transfer to be the only action.
        // It cannot be hijacked by other actions batched with the transfer.
        AccountType::UniversalAccount => true,
    }
}
```

**File:** core/primitives/src/receipt.rs (L493-510)
```rust
    /// Generates a receipt with a transfer from system for a given balance without a receipt_id.
    /// This should be used for token refunds instead of gas refunds.
    /// It doesn't refund the allowance of the access key. For gas refunds use `new_gas_refund`.
    pub fn new_balance_refund(receiver_id: &AccountId, refund: Balance) -> Self {
        Receipt::V0(ReceiptV0 {
            predecessor_id: "system".parse().unwrap(),
            receiver_id: receiver_id.clone(),
            receipt_id: CryptoHash::default(),
            receipt: ReceiptEnum::Action(ActionReceipt {
                signer_id: "system".parse().unwrap(),
                signer_public_key: PublicKey::empty(KeyType::ED25519),
                gas_price: Balance::ZERO,
                output_data_receivers: vec![],
                input_data_ids: vec![],
                actions: vec![Action::Transfer(TransferAction { deposit: refund })],
            }),
        })
    }
```

**File:** runtime/runtime/src/lib.rs (L1047-1055)
```rust
        let gas_refund_result = if receipt.predecessor_id().is_system() {
            // If the refund fails tokens are burned.
            if result.result.is_err() {
                stats.balance.other_burnt_amount = safe_add_balance(
                    stats.balance.other_burnt_amount,
                    total_deposit(&action_receipt.actions())?,
                )?
            }
            GasRefundResult::default()
```

**File:** protocol-model/spec/runtime-execution.md (L152-152)
```markdown
- **Refund receipts are free**: system-predecessor receipts burn zero gas; a failed refund burns its deposit into `other_burnt_amount` rather than refunding (`runtime/runtime/src/lib.rs:929`, `:972`).
```

**File:** runtime/runtime/src/tests/apply.rs (L6354-6357)
```rust

        // The beneficiary has to exist, otherwise the balance transfer the delete
        // sends it would come straight back as a refund.
        let beneficiary: AccountId = "beneficiary.near".parse().unwrap();
```
