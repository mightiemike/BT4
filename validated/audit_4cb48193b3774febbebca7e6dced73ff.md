## Analog Found

### Title
DeleteAccount with a beneficiary that doesn't exist permanently burns the deleted account's funds - (File: runtime/runtime/src/actions.rs, runtime/runtime/src/lib.rs)

### Summary
`DeleteAccountAction::beneficiary_id` is validated only for well-formed `AccountId` syntax, never for existence. When the beneficiary account does not exist, the payout receipt (a system "refund") fails, and instead of being returned to anyone, the account's entire remaining balance is permanently burned. This mirrors the Gitcoin `setReadyForPayout()` bug class: a payout path lacks a validity/reachability check on the recipient, so funds sent there are irrecoverably destroyed rather than reverting safely.

### Finding Description
`action_delete_account` builds the token payout to the beneficiary as a **balance-refund receipt** rather than a normal transfer: [1](#0-0) 

`Receipt::new_balance_refund` sets `predecessor_id = "system"`, which marks the receipt as a refund: [2](#0-1) 

When this refund receipt is later applied, `apply_action` computes `is_refund = receipt.predecessor_id().is_system()` and forces `implicit_account_creation_eligible = false` for any refund, regardless of the target account's format: [3](#0-2) 

`check_account_existence` then routes to `check_transfer_to_nonexisting_account`, which explicitly documents that "refunds don't automatically create accounts" and returns `AccountDoesNotExist` whenever the beneficiary account is not present in state: [4](#0-3) 

Finally, when a refund receipt (`predecessor_id == "system"`) fails, the runtime burns the deposit outright instead of returning it anywhere: [5](#0-4) 

So the sequence is: user calls `DeleteAccount{ beneficiary_id }` on their own account → runtime emits a "balance refund" receipt carrying the account's full balance to `beneficiary_id` → if `beneficiary_id` is syntactically valid but does not exist (a never-created named account, or an implicit/hex account that was never funded), the receipt fails with `AccountDoesNotExist` → because it is a refund, the entire deposit is added to `other_burnt_amount` and permanently removed from total supply, never delivered to the signer, the beneficiary, or anyone else.

### Impact Explanation
This is a direct, unprivileged-signer-reachable path (any account holder deleting their own account) that results in **permanent, irrecoverable loss of the account's funds**, i.e. "permanently frozen funds" via burning — the exact impact class the analog report cites for `setReadyForPayout()` sending to `address(0)`. No admin, validator, or protocol role is required; a single `DeleteAccount` action with a plausible-looking but non-existent `beneficiary_id` (e.g., a typo'd account, or a sub-account that was never created) destroys the balance instead of erroring out before the account is deleted or refunding the signer.

### Likelihood Explanation
High likelihood of accidental triggering: `beneficiary_id` only needs to pass `AccountId` syntax validation (per `docs/RuntimeSpec/Actions.md`'s `InvalidAccountId` check), not existence validation, so a simple typo in a wallet UI, SDK, or CLI when specifying the beneficiary for account deletion silently burns the user's balance. It can also be exploited deliberately by a malicious dApp/relayer constructing a `DeleteAccount` action (directly or via a meta-transaction/`Delegate` action) with an intentionally non-existent beneficiary to grief a user into losing funds during account cleanup.

### Recommendation
Before emitting the balance-refund receipt in `action_delete_account`, verify that `beneficiary_id` corresponds to an existing account (or fall back to burning only as an explicit, clearly-documented/deliberate policy rather than a silent side effect of "refund failure"). Alternatively, treat the beneficiary payout as a normal `Transfer` receipt (not a refund) so that on failure the standard deposit-refund-to-predecessor path is exercised, or require `DeleteAccount` validation to reject non-existent, non-implicit `beneficiary_id`s at action-creation time, analogous to adding the missing `address(0)` check recommended in the source report.

### Proof of Concept
1. Alice owns an account `alice.near` with balance `B`.
2. Alice submits `SignedTransaction { actions: [Action::DeleteAccount(DeleteAccountAction{ beneficiary_id: "never-created-account.near" })] }` (a syntactically valid `AccountId` that has never been registered), reachable via any JSON-RPC transaction submission.
3. `action_delete_account` deletes `alice.near` and emits `Receipt::new_balance_refund("never-created-account.near", B)`.
4. This receipt executes with `predecessor_id = "system"`; since `never-created-account.near` doesn't exist, `check_account_existence` → `check_transfer_to_nonexisting_account` returns `ActionErrorKind::AccountDoesNotExist`.
5. Because the receipt's predecessor is `"system"`, the runtime treats the failure as a refund failure and adds `B` to `stats.balance.other_burnt_amount` — `B` yoctoNEAR is permanently removed from total supply, never reaching Alice, the "beneficiary", or any validator/treasury account.

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

**File:** runtime/runtime/src/lib.rs (L547-562)
```rust
        let account_id = receipt.receiver_id();
        let is_refund = receipt.predecessor_id().is_system();
        let is_the_only_action = actions.len() == 1;
        let implicit_account_creation_eligible = is_the_only_action && !is_refund;

        // Account validation
        if let Err(e) = check_account_existence(
            action,
            account,
            account_id,
            &apply_state.config,
            implicit_account_creation_eligible,
        ) {
            result.result = Err(e);
            return Ok(result);
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
