### Title
DeleteAccount permanently burns the account's balance when `beneficiary_id` does not exist, with no existence check performed before the account is irreversibly deleted - ([File: runtime/runtime/src/actions.rs])

### Summary
`DeleteAccountAction::beneficiary_id` is only validated for *format* (a syntactically valid account id) before execution, never for *existence*. `action_delete_account` unconditionally removes the source account and, only afterward, queues a system balance-refund receipt to `beneficiary_id`. If that account does not exist (typo, deleted account, or an account that was never created), the refund receipt fails and, per the protocol's refund semantics, the funds are burnt rather than returned to anyone — while the source account is already gone. This mirrors the reported ERC721 bug class: an action that irreversibly disposes of a resource (mints to / deletes an account, transferring value to a "receiver") without first confirming the receiver can actually accept the transfer.

### Finding Description
`validate_delete_action` only calls `validate_action_account_id(&action.beneficiary_id)`, which checks the id is syntactically valid — it does not check that the account exists: [1](#0-0) 

`action_delete_account` then executes unconditionally: it computes the account's balance, pushes a `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)`, and immediately removes the account from state (`*account = None`) — regardless of whether the beneficiary receipt will ever succeed: [2](#0-1) [3](#0-2) 

The generated refund receipt is a system-predecessor receipt, and `implicit_creation_allowed` explicitly forbids refunds from creating any account type, named or implicit: [4](#0-3) 

so if `beneficiary_id` does not exist, `check_account_existence` rejects the `Transfer` action inside that refund receipt with `AccountDoesNotExist`. Per the documented refund model, a refund receipt that fails to execute has its deposit burnt, not returned to anyone:
> "If the execution of a refund fails, the refund amount is burnt." — `docs/RuntimeSpec/Refunds.md:12`

By the time this refund receipt executes, the original account (the only entity that could otherwise reclaim the funds) has already been deleted in the same action, so the loss is final and unrecoverable.

### Impact Explanation
Any unprivileged transaction signer who submits a `DeleteAccount` action (directly, via a meta-transaction/`Delegate` action, or via a contract's `promise_batch_action_delete_account` host call) with a non-existent `beneficiary_id` causes their entire remaining account balance to be permanently burnt with no recovery path. This satisfies the "permanently frozen funds" impact class: value is unconditionally destroyed as a direct consequence of a missing existence check on the receiving account before the irreversible action (account deletion) is committed — exactly analogous to minting an NFT to an incompatible receiver and losing the token forever.

### Likelihood Explanation
Trivially reachable by any account holder in a single transaction — no special privileges, validator status, or network/timing conditions are required. It can be triggered accidentally (typo in `beneficiary_id`, referencing an account that was deleted between construction and execution of the transaction) or deliberately by a relayer/dApp misconfiguration, and via meta-transactions where a sender's `beneficiary_id` is attacker/relayer supplied.

### Recommendation
Before permanently removing the account in `action_delete_account`, verify that `beneficiary_id` corresponds to an existing (and, ideally, initialized) account, mirroring the check already performed for `Transfer` actions in `check_account_existence`/`implicit_creation_allowed`. If the beneficiary does not exist, the `DeleteAccount` action itself should fail with an `ActionErrorKind` (e.g., a new `BeneficiaryAccountDoesNotExist` kind) instead of proceeding to delete the account and later burning the refund.

### Proof of Concept
1. Account `alice.near` holds a balance and signs `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "nonexistent.near" })` where `nonexistent.near` has never been created.
2. `validate_delete_action` accepts the action because `nonexistent.near` is a syntactically valid `AccountId`.
3. `action_delete_account` runs: it computes `alice.near`'s balance, pushes `Receipt::new_balance_refund("nonexistent.near", balance)`, and deletes `alice.near` from state (`*account = None`).
4. The refund receipt executes with `predecessor_id == "system"`; `check_account_existence` rejects the embedded `Transfer` action against `nonexistent.near` via `implicit_creation_allowed` (refunds never create accounts) with `AccountDoesNotExist`.
5. Per `Refunds.md`, the failed refund's deposit is burnt (`other_burnt_amount`), and since `alice.near` no longer exists, the entire balance is unrecoverable.

This flow is directly exercised (for the "exists" happy path) by the existing test `delete_after_init_removes_account`, whose own comment confirms the failure mode for the non-existent case: "The beneficiary has to exist, otherwise the balance transfer the delete sends would come straight back as a refund [and be burnt on failure]." [5](#0-4)

### Citations

**File:** runtime/runtime/src/action_validation.rs (L447-451)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
```

**File:** runtime/runtime/src/actions.rs (L330-340)
```rust
pub(crate) fn action_delete_account(
    state_update: &mut TrieUpdate,
    account: &mut Option<Account>,
    actor_id: &mut AccountId,
    receipt: &Receipt,
    result: &mut ActionResult,
    account_id: &AccountId,
    delete_account: &DeleteAccountAction,
    config: &RuntimeConfig,
    current_protocol_version: ProtocolVersion,
) -> Result<(), StorageError> {
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

**File:** runtime/runtime/src/tests/apply.rs (L6354-6357)
```rust

        // The beneficiary has to exist, otherwise the balance transfer the delete
        // sends it would come straight back as a refund.
        let beneficiary: AccountId = "beneficiary.near".parse().unwrap();
```
