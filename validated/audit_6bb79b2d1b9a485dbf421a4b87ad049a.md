### Title
Loss of funds via unchecked `beneficiary_id` on `DeleteAccountAction` — funds are permanently burnt if the beneficiary does not exist - (`runtime/runtime/src/actions.rs`, `runtime/runtime/src/lib.rs`)

### Summary
The Solidity report flags `RoundImplementation.initialize`/`updateRoundFeeAddress` for accepting an unchecked recipient address, which can permanently strand funds sent to `address(0)`. The nearcore analog is `Action::DeleteAccount`'s `beneficiary_id` field: the protocol validates only that it is a *syntactically* well-formed account id, never that the account actually exists (or can be created). Because the resulting balance transfer is issued as a system "refund" receipt, and refund receipts are the one receipt shape that can never implicitly create an account, a `beneficiary_id` pointing at a non-existent account causes the whole remaining balance of the deleted account to be permanently burned instead of delivered or returned.

### Finding Description
`DeleteAccountAction` is defined with a single field, `beneficiary_id: AccountId` (see `docs/RuntimeSpec/Actions.md:278-285`). Action validation only checks that it is a well-formed account id string: [1](#0-0) 

`action_delete_account` then unconditionally schedules a payout receipt to that id, with no check that the account exists: [2](#0-1) 

That payout is built via `Receipt::new_balance_refund`, i.e. it is emitted as a **refund receipt** (`predecessor_id == "system"`). Refund/system receipts are the one receipt shape explicitly barred from implicitly creating any account kind — named, NEAR-implicit, ETH-implicit, deterministic, or universal: [3](#0-2) [4](#0-3) 

So if `beneficiary_id` names an account that is not already present on chain, the transfer inside that refund receipt fails `check_account_existence` with `AccountDoesNotExist`. Crucially, when a *refund* receipt fails, the runtime does not retry or bounce the deposit anywhere — it burns it: [5](#0-4) 

This is documented as intended behavior for refunds in general (`docs/RuntimeSpec/Refunds.md:10-13`: "If the execution of a refund fails, the refund amount is burnt.") and is explicitly called out for the delete-account path in test comments: [6](#0-5) 

An existing test, `delete_after_init_removes_account`, even documents this as a known trap ("The beneficiary has to exist, otherwise the balance transfer the delete sends it would come straight back as a refund" — and that refund, being system-predecessor, would itself fail and burn).

The only mitigations present are unrelated to the beneficiary's existence: `DeleteActionMustBeFinal`, the no-locked-stake check, and the account-size cap (`MAX_ACCOUNT_DELETION_STORAGE_USAGE`). None of them validate that `beneficiary_id` is a live, receivable account.

### Impact Explanation
Any account holder (an ordinary transaction signer, or a contract that appends `promise_batch_action_delete_account` to a receipt) can trigger permanent, unrecoverable destruction of their own account's full remaining NEAR balance simply by naming a `beneficiary_id` that does not currently exist — e.g. a typo'd account name, an account that was never created, or one that was deleted between construction and execution of the transaction/receipt. Rather than the transaction failing safely (which would be the case for an ordinary `Transfer` action, which is rejected up front for a non-existent named receiver), the delete succeeds, the account is destroyed, and its balance is silently converted into `other_burnt_amount` — i.e., burnt from total supply with no recovery path. This is a direct "permanently frozen/lost funds" outcome, matching the Sherlock report's root cause (missing recipient-liveness/validity check on a fund-transfer target field) even though nearcore has no `address(0)` concept — the exploitable gap here is "syntactically valid but non-existent account id."

### Likelihood Explanation
Likelihood is high for accidental loss (user/contract typos in `beneficiary_id`, or beneficiary accounts that get deleted in the same or an earlier chunk before the delete-account receipt executes) and it requires no special privilege — any single self-submitted `DeleteAccount` transaction or `promise_batch_action_delete_account` host call reaches this path. It is a protocol-level design gap rather than a race that depends on adversarial timing, though a malicious relayer/contract composing a batch could also deliberately target a not-yet-existing id to grief a user's balance during account deletion flows (e.g. meta-transactions, NEP-366).

### Recommendation
Before emitting the beneficiary payout as a refund receipt in `action_delete_account`, verify that `beneficiary_id` refers to an existing account (or is the deleting account's own predecessor, or otherwise falls back to a safe target) and reject the `DeleteAccount` action with a clear error (e.g. a new `ActionErrorKind::BeneficiaryAccountDoesNotExist`) if it does not, rather than allowing the account to be destroyed and its balance silently burnt via a failed system refund.

### Proof of Concept
1. Create account `alice.near` with balance `B`.
2. Submit a transaction from `alice.near` to itself containing `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "nonexistent.near" })`, where `nonexistent.near` has never been created.
3. `action_delete_account` removes `alice.near` and schedules `Receipt::new_balance_refund("nonexistent.near", B)`.
4. That receipt has `predecessor_id == "system"`; `check_account_existence` rejects the `Transfer` because `implicit_creation_allowed` returns `false` for a refund receipt regardless of account type.
5. Per `runtime/runtime/src/lib.rs:1047-1054`, the failed refund's deposit `B` is added to `stats.balance.other_burnt_amount` — permanently removed from total supply, unrecoverable by `alice.near` or anyone else. [2](#0-1) [5](#0-4)

### Citations

**File:** runtime/runtime/src/action_validation.rs (L447-451)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
```

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

**File:** runtime/runtime/src/actions.rs (L2590-2597)
```rust
        // Refunds are free, and account deletion with a beneficiary makes one, so
        // no kind may be created by one however lonely the transfer is.
        for account_type in ALL {
            assert!(
                !implicit_creation_allowed(account_type, refund),
                "{account_type:?} must not be created by a refund"
            );
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

**File:** runtime/runtime/src/tests/apply.rs (L6355-6357)
```rust
        // The beneficiary has to exist, otherwise the balance transfer the delete
        // sends it would come straight back as a refund.
        let beneficiary: AccountId = "beneficiary.near".parse().unwrap();
```
