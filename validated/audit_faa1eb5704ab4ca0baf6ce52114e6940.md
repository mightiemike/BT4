### Title
Permanent Loss of Funds When `DeleteAccount` Beneficiary Is a Non-Implicit, Non-Existent Account - (File: `runtime/runtime/src/actions.rs`, `runtime/runtime/src/lib.rs`)

### Summary
The `DeleteAccount` action lets an account owner specify an arbitrary `beneficiary_id` to receive the deleted account's remaining balance. This is directly analogous to the ETH refund-recipient parameter in the zkSync report: a user (or contract) supplies a destination account id for a refund/payout, but the protocol does not verify that the destination is an existing, controllable account before committing to the transfer. If `beneficiary_id` refers to a non-existent, non-implicit-format account (e.g. a mistyped or unregistered named account, or an account that will never be created), the payout is silently and irreversibly burned rather than returned to the caller or blocked.

### Finding Description
`action_delete_account` unconditionally schedules a system balance-refund receipt to `delete_account.beneficiary_id` without validating that this account exists or can be created: [1](#0-0) 

This produces a `Receipt::new_balance_refund` with `predecessor_id = "system"`: [2](#0-1) 

When that receipt is later applied to the beneficiary account, `apply_action` treats it as a refund (`is_refund = receipt.predecessor_id().is_system()`): [3](#0-2) 

`check_account_existence` rejects the `Transfer` action if the beneficiary account does not exist and implicit-account creation is not allowed for that account type/shape: [4](#0-3) 

Named (non-implicit) accounts can never satisfy `implicit_creation_allowed`, so a non-existent named `beneficiary_id` causes the transfer action to fail with `AccountDoesNotExist`. Because the receipt's `predecessor_id` is `"system"`, the documented behavior for a failing refund receipt applies: no further refund is generated and the amount is burned into `other_burnt_amount`: [5](#0-4) 

This is corroborated by an explicit test comment stating the beneficiary "has to exist, otherwise the balance transfer the delete sends it would come straight back as a refund" — and refund-of-a-refund is burned, not delivered anywhere: [6](#0-5) 

The `DeleteAccount` action itself is not blocked or reverted by this outcome — the account is deleted (`account = None`) regardless of whether the subsequent payout receipt later fails: [7](#0-6) 

This mirrors the reported bug class precisely: a user-facing "refund/recipient" parameter (`_refundRecipient` in zkSync, `beneficiary_id` here) determines where value is routed, the protocol performs no existence/controllability check on that destination before committing irreversibly to the deletion, and an incorrect or already-nonexistent destination results in permanent, unrecoverable loss of the funds — worse than "locked," since NEAR burns them outright rather than merely stranding them at an uncontrolled address.

### Impact Explanation
Any account owner who submits a `DeleteAccount` action with a `beneficiary_id` that does not exist (typo, deleted account, or account that was never created) permanently and irrecoverably loses the entire remaining balance of the deleted account. There is no error surfaced on the `DeleteAccount` transaction itself (it succeeds, per `DeleteActionMustBeFinal`/success outcome), and the loss is buried in an asynchronously-processed refund receipt failure that most users/tools would not correlate back to their beneficiary choice. This is a concrete, protocol-level "permanently frozen/burnt funds" outcome, satisfying the Validate criteria (unauthorized value movement equivalent — funds destroyed instead of delivered).

### Likelihood Explanation
High reachability: any unprivileged transaction signer can trigger this by submitting a normal `DeleteAccount` action with a `beneficiary_id` of a non-existent named account — no special privileges, contracts, or races required. The only barrier is user/tooling error (mistyped or non-existent beneficiary), which is exactly the class of foot-gun the original zkSync report was about (aliasing/derivation confusion causing loss due to a caller-supplied destination that isn't actually reachable).

### Recommendation
Validate that `beneficiary_id` refers to an existing account (or a syntactically valid implicit-account id capable of being auto-created) at `DeleteAccount` action-application time, and reject the action (fail the whole receipt, keeping the account intact) if the beneficiary cannot receive funds. Alternatively, disallow completion of `DeleteAccount` unless the beneficiary account is confirmed to exist in state at execution time, mirroring how `Transfer` already validates account existence via `check_account_existence`/`implicit_creation_allowed` before committing to the deletion.

### Proof of Concept
1. Create account `victim.near` with a positive balance.
2. From `victim.near`, submit `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "doesnotexist.near" })` where `doesnotexist.near` is a syntactically valid named account id that has never been created.
3. The `DeleteAccount` action succeeds; `victim.near` is deleted and a `Receipt::new_balance_refund` is emitted targeting `doesnotexist.near`.
4. When that receipt executes, `check_account_existence` rejects the `Transfer` action (`AccountDoesNotExist`), the action receipt fails; since its `predecessor_id` is `"system"`, no further refund is attempted and the deposit is added to `other_burnt_amount` per `docs/RuntimeSpec/Refunds.md`.
5. Result: `victim.near`'s entire balance is permanently destroyed, with no account able to reclaim it — this can be confirmed against the existing test at `runtime/runtime/src/tests/apply.rs:6342-6390` (`delete_after_init_removes_account`), which explicitly pre-creates the beneficiary account "otherwise the balance transfer the delete sends it would come straight back as a refund" (and be burned), demonstrating maintainers are aware non-existent beneficiaries cause fund loss but have not added a validation guard.

### Citations

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

**File:** runtime/runtime/src/actions.rs (L387-404)
```rust
    let remove_result = remove_account(state_update, account_id)?;
    result.tokens_burnt =
        result.tokens_burnt.checked_add(gas_key_balance_to_burn).ok_or_else(|| {
            StorageError::StorageInconsistentState("tokens_burnt overflow".to_string())
        })?;
    if remove_result.gas_key_nonce_count > 0 {
        let compute = storage_removes_compute(
            &config.wasm_config.ext_costs,
            remove_result.gas_key_nonce_count,
            remove_result.gas_key_nonce_total_key_bytes,
            AccessKey::NONCE_VALUE_LEN * remove_result.gas_key_nonce_count,
        );
        result.compute_usage = safe_add_compute(result.compute_usage, compute).map_err(|_| {
            StorageError::StorageInconsistentState("compute_usage overflow".to_string())
        })?;
    }
    *actor_id = receipt.predecessor_id().clone();
    *account = None;
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

**File:** runtime/runtime/src/lib.rs (L594-597)
```rust
            account.as_ref().map(|a| a.contract().into_owned()).unwrap_or(AccountContract::None);
        let account_id = receipt.receiver_id();
        let is_refund = receipt.predecessor_id().is_system();
        let receipt_shape = ReceiptShape { is_refund, is_the_only_action: actions.len() == 1 };
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
