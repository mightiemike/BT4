Confirmed: `validate_delete_action` at `runtime/runtime/src/action_validation.rs:447-451` only calls `validate_action_account_id` (syntactic `AccountId::validate` format check), with no check that `beneficiary_id` refers to an account that actually exists. This confirms the analog is real and reachable by any unprivileged transaction signer.

### Title
DeleteAccount with a non-existent `beneficiary_id` permanently burns the account's entire balance instead of failing or refunding - ([File: runtime/runtime/src/actions.rs])

### Summary
`Action::DeleteAccount` lets any account owner specify an arbitrary `beneficiary_id` to receive their remaining balance. Only the syntactic validity of `beneficiary_id` is checked before execution (`validate_delete_action`, `runtime/runtime/src/action_validation.rs:447-451`), never its existence. If the id does not correspond to an existing account (e.g., a typo, a since-deleted account, or an account that was never created), the entire balance is silently and permanently burned rather than returned to the signer or the transaction failing outright — directly analogous to the reported bond-teller bug where an unvalidated recipient address caused payout tokens to be burned.

### Finding Description
`action_delete_account` (`runtime/runtime/src/actions.rs:330-386`) takes the account's current balance and unconditionally queues a refund receipt to the caller-supplied beneficiary before deleting the account: [1](#0-0) 

The refund is built with `Receipt::new_balance_refund`, which sets `predecessor_id = "system"`, marking it as a refund receipt: [2](#0-1) 

When this refund receipt executes, `check_account_existence` treats it as `is_refund = true`. For a `Transfer` action to a missing account, `implicit_creation_allowed` explicitly refuses to create the account when the receipt is a refund: [3](#0-2) [4](#0-3) 

So the refund receipt itself fails with `AccountDoesNotExist`. Because it is a refund (`predecessor_id.is_system()`), the runtime's own rule for failed refunds burns the funds rather than generating a further refund: [5](#0-4) 

This is also spelled out in the docs: "If the execution of a refund fails, the refund amount is burnt," and is confirmed by an existing test comment noting that a `DeleteAccount` beneficiary "has to exist, otherwise the balance transfer the delete sends it would come straight back as a refund" (i.e., it fails and is burnt, since refunds can't create accounts): [6](#0-5) [7](#0-6) 

The only pre-execution validation of `beneficiary_id` is a syntactic format check, with no existence check: [8](#0-7) [9](#0-8) 

By the time the beneficiary's non-existence is discovered (when the refund receipt executes), the `DeleteAccount` action has already committed and the source account and its balance are gone — there is no path back to the original owner.

### Impact Explanation
Any account holder submitting an ordinary `DeleteAccount` transaction — no privileged role required — can have their entire remaining NEAR balance permanently and irrecoverably burned if `beneficiary_id` does not exist at execution time (a typo, a beneficiary account deleted between the time the transaction was authored and applied, or a beneficiary account that was simply never created). This is concrete, unauthorized/unintended token destruction and permanently frozen (burnt) funds resulting from an unvalidated user-supplied recipient, matching the bug class in the referenced report (funds sent/burned to an unvalidated recipient with no existence check).

### Likelihood Explanation
Reachable directly by a single unprivileged, self-signed transaction with no special conditions: any user who submits `DeleteAccount { beneficiary_id }` where `beneficiary_id` does not exist triggers the loss. This can happen accidentally (typo in a beneficiary id, especially since accounts need not exist to pass syntax validation) or be induced by front-running/racing the deletion of the intended beneficiary account before the `DeleteAccount` receipt executes.

### Recommendation
Validate that `beneficiary_id` corresponds to an existing account before allowing `DeleteAccount` to proceed (e.g., during `action_delete_account` execution, or require the beneficiary account to be read first and reject with an `ActionError` such as `BeneficiaryAccountDoesNotExist` if absent), so the whole receipt fails cleanly (rolling back the deletion) instead of deleting the account and only discovering the missing beneficiary during a refund that is treated as unrecoverable.

### Proof of Concept
1. Account `alice.near` holds a balance and has no locked stake.
2. `alice.near` signs and submits `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "nonexistent-account-name" })` where `nonexistent-account-name` is a syntactically valid but never-created (or since-deleted) account id.
3. `validate_delete_action` accepts the action (format-only check).
4. `action_delete_account` deletes `alice.near` and queues `Receipt::new_balance_refund(&"nonexistent-account-name", account_balance)`.
5. When that refund receipt executes, `check_account_existence` finds no account and `implicit_creation_allowed` returns `false` because `is_refund = true`, producing `AccountDoesNotExist`.
6. Because the receipt's predecessor is `"system"` (a refund) and it failed, `apply_action_receipt` adds `account_balance` to `stats.balance.other_burnt_amount` instead of refunding it to `alice.near`.
7. `alice.near`'s balance is permanently destroyed; there is no account left to reimburse.

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

**File:** core/primitives/src/receipt.rs (L496-510)
```rust
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

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```

**File:** runtime/runtime/src/tests/apply.rs (L6354-6357)
```rust

        // The beneficiary has to exist, otherwise the balance transfer the delete
        // sends it would come straight back as a refund.
        let beneficiary: AccountId = "beneficiary.near".parse().unwrap();
```

**File:** runtime/runtime/src/action_validation.rs (L447-451)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
```

**File:** runtime/runtime/src/action_validation.rs (L602-608)
```rust
fn validate_action_account_id(account_id: &AccountId) -> Result<(), ActionsValidationError> {
    AccountId::validate(account_id.as_str()).map_err(|_| {
        ActionsValidationError::InvalidAccountId { account_id: account_id.to_string() }
    })?;

    Ok(())
}
```
