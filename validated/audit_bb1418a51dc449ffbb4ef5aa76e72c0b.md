Confirmed: `validate_delete_action` (`runtime/runtime/src/action_validation.rs:388-392`) only checks that `beneficiary_id` is a syntactically valid `AccountId` — it never checks that the account actually exists or is reachable. That validation gap is what makes the following analog exploitable.

### Title
`DeleteAccount`'s balance refund to `beneficiary_id` is silently burnt when the beneficiary account does not exist - (File: `runtime/runtime/src/actions.rs`)

### Summary
`action_delete_account` unconditionally pays the deleted account's remaining balance to `delete_account.beneficiary_id` as a system-refund receipt, without ever checking that `beneficiary_id` refers to an existing, reachable account. When it does not, the resulting transfer fails and the whole balance is permanently burnt.

### Finding Description
When a `DeleteAccount` action executes, the runtime pays out the account's balance via `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)` [1](#0-0) . This receipt is a refund (`predecessor_id == "system"`) as defined in `Receipt::new_balance_refund` [2](#0-1) .

The only stateless validation performed on `beneficiary_id` is a syntactic account-id format check, not an existence check: `validate_delete_action` calls `validate_action_account_id(&action.beneficiary_id)`, which only calls `AccountId::validate` [3](#0-2) .

When the generated balance-refund receipt is later applied against `beneficiary_id`, `check_account_existence` runs `check_transfer_to_nonexisting_account` for the `Transfer` action if the account doesn't exist [4](#0-3) . That function explicitly documents that refunds do not get to auto-create implicit accounts: "Refunds don't automatically create accounts, because refunds are free... Account deletion with beneficiary creates a refund, so it'll not create a new account." [5](#0-4) . So if `beneficiary_id` is not an already-existing account (a typo'd account, an unregistered/unused account id, or even a syntactically-valid implicit account id that was never funded), the refund receipt execution fails with `AccountDoesNotExist`.

Because this receipt has `predecessor_id().is_system() == true`, the runtime's refund-failure path burns the entire deposit instead of returning it anywhere: "If the refund fails tokens are burned" — `stats.balance.other_burnt_amount` absorbs `total_deposit(&action_receipt.actions())` [6](#0-5) . This matches the documented invariant "If the execution of a refund fails, the refund amount is burnt" [7](#0-6) .

### Impact Explanation
This is directly analogous to the `L2ECOBridge.withdraw` bug: a value-destination address/account-id is taken from user/contract input and used for a fund payout without validating that it is a live, controllable account. Any unprivileged transaction signer, or any contract issuing a `DeleteAccount` action on its own behalf (e.g., self-destructing contracts, wallets closing an account, or a relayer/meta-transaction signer performing account cleanup), can — through a simple typo, a copy of an account that was never created, or targeting an implicit-style id that has no funded key — cause their entire remaining account balance to be permanently and irreversibly burnt rather than transferred. This is a concrete, transaction-triggered loss of user funds (not merely gas or a low-severity issue): the "delete account here / refund me elsewhere" primitive silently destroys value instead of failing the whole action or reverting.

### Likelihood Explanation
Likelihood is meaningfully high because:
- Any unprivileged signer can submit a `DeleteAccount` action with an arbitrary `beneficiary_id` — no special permission needed beyond owning the account being deleted.
- The only check enforced is a cheap syntactic format check, not existence; nothing in the flow warns or fails at submission time even though the destination is guaranteed not to receive funds if it never existed.
- The mistake vector (typo, wrong account, unregistered/unfunded implicit account id) is exactly the kind of unprivileged, single-transaction user error class this report class targets, mirroring the original bridge bug where a valid-looking destination silently swallows funds.

### Recommendation
Before generating the balance-refund receipt in `action_delete_account`, verify that `beneficiary_id` corresponds to an existing account in state (i.e., perform an account-existence lookup analogous to `check_account_existence`/`check_transfer_to_nonexisting_account`) and fail the `DeleteAccount` action outright (returning an `ActionError` such as `AccountDoesNotExist`) if the beneficiary account does not exist, rather than letting the funds be silently burnt after the account has already been deleted.

### Proof of Concept
1. Create account `victim.near` with a non-zero balance and a full access key.
2. As `victim.near`, submit a transaction whose sole/final action is `DeleteAccount(beneficiary_id = "typo123.near")` where `typo123.near` does not exist on chain (this passes `validate_delete_action`'s format check trivially, per `runtime/runtime/src/action_validation.rs:388-392`).
3. `action_delete_account` removes `victim.near` and enqueues `Receipt::new_balance_refund("typo123.near", account_balance)` (`runtime/runtime/src/actions.rs:349-355`).
4. When this system-refund receipt is applied, `check_account_existence`/`check_transfer_to_nonexisting_account` rejects it with `AccountDoesNotExist` because it is a refund and cannot auto-create an implicit account (`runtime/runtime/src/actions.rs:791-849`).
5. Because the failing receipt's predecessor is `system`, the runtime burns the entire `account_balance` into `other_burnt_amount` instead of returning it to anyone (`runtime/runtime/src/lib.rs:926-934`), permanently destroying `victim.near`'s funds.

### Citations

**File:** runtime/runtime/src/actions.rs (L349-355)
```rust
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
```

**File:** runtime/runtime/src/actions.rs (L791-798)
```rust
        Action::Transfer(_) => {
            if account.is_none() {
                return check_transfer_to_nonexisting_account(
                    config,
                    account_id,
                    implicit_account_creation_eligible,
                );
            }
```

**File:** runtime/runtime/src/actions.rs (L829-849)
```rust
fn check_transfer_to_nonexisting_account(
    config: &RuntimeConfig,
    account_id: &AccountId,
    implicit_account_creation_eligible: bool,
) -> Result<(), ActionError> {
    if implicit_account_creation_eligible
        && account_is_implicit(account_id, config.wasm_config.eth_implicit_accounts)
    {
        // OK. It's implicit account creation.
        // Notes:
        // - Transfer action has to be the only action in the transaction to avoid
        // abuse by hijacking this account with other public keys or contracts.
        // - Refunds don't automatically create accounts, because refunds are free and
        // we don't want some type of abuse.
        // - Account deletion with beneficiary creates a refund, so it'll not create a
        // new account.
        Ok(())
    } else {
        Err(ActionErrorKind::AccountDoesNotExist { account_id: account_id.clone() }.into())
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

**File:** runtime/runtime/src/action_validation.rs (L388-392)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
```

**File:** runtime/runtime/src/lib.rs (L926-934)
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

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```
