### Title
Missing `beneficiary_id == account_id` validation in `action_delete_account` causes irreversible fund loss — (`runtime/runtime/src/actions.rs`)

---

### Summary

`action_delete_account` creates a system balance-refund receipt addressed to `beneficiary_id`, then immediately removes the account from state. No guard checks whether `beneficiary_id` equals the account being deleted. When the refund receipt later executes, the target account no longer exists, the transfer fails, and the runtime burns the entire balance into `other_burnt_amount`. Any unprivileged user can trigger this on their own account by submitting a `DeleteAccount` action with `beneficiary_id` set to their own account ID.

---

### Finding Description

**Validation gap — `action_validation.rs`**

`validate_action_with_mode` dispatches `Action::DeleteAccount` to `validate_delete_action`, passing only the action struct, not the receipt's `receiver` (the account being deleted): [1](#0-0) 

`validate_delete_action` only checks that `beneficiary_id` is a syntactically valid account ID: [2](#0-1) 

There is no check that `beneficiary_id != receiver`. The `receiver` is available in `validate_action_with_mode` but is never forwarded to the delete-account validator. [3](#0-2) 

**Execution path — `actions.rs`**

`action_delete_account` unconditionally emits a balance-refund receipt to `beneficiary_id`, then removes the account: [4](#0-3) 

`Receipt::new_balance_refund` stamps `predecessor_id = "system"` on the receipt, making it a system refund receipt: [5](#0-4) 

**Burn path — `lib.rs`**

When a system-predecessor receipt fails, the runtime burns its deposit into `other_burnt_amount` instead of re-refunding: [6](#0-5) 

When `beneficiary_id == account_id`, the refund receipt's `Transfer` action hits `check_account_existence`, finds the account absent, and returns `AccountDoesNotExist`. Because the receipt is a system receipt, the failure path above burns the balance. [7](#0-6) 

---

### Impact Explanation

A user who submits `DeleteAccount { beneficiary_id: own_account_id }` loses their entire liquid balance permanently. The funds are not recoverable: they are credited to `other_burnt_amount` (effectively removed from total supply). The account is also gone, so there is no rollback. This is a direct, irreversible loss of funds reachable by any unprivileged user acting on their own account.

---

### Likelihood Explanation

Likelihood is low-to-medium. The mistake is natural: a user who wants to "delete and keep the money" may intuitively set `beneficiary_id` to themselves, not realising the account is erased before the refund receipt executes. The default `delete_account` helper in the integration-test user library already exhibits this pattern — it sets `beneficiary_id = signer_id`, which equals `account_id` for self-deletion: [8](#0-7) 

Any wallet or SDK that mirrors this pattern exposes real users to the loss.

---

### Recommendation

Pass `receiver` into `validate_delete_action` and reject the action when `beneficiary_id == receiver`:

```rust
// runtime/runtime/src/action_validation.rs

// In validate_action_with_mode:
Action::DeleteAccount(a) => validate_delete_action(a, receiver),

// Updated validator:
fn validate_delete_action(
    action: &DeleteAccountAction,
    receiver: &AccountId,
) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;
    if &action.beneficiary_id == receiver {
        return Err(ActionsValidationError::InvalidAccountId {
            account_id: action.beneficiary_id.to_string(),
        });
    }
    Ok(())
}
```

A dedicated error variant (`DeleteAccountBeneficiaryIsReceiver`) would be cleaner and more informative than reusing `InvalidAccountId`.

---

### Proof of Concept

**Attacker input (transaction):**
```
signer_id:    alice.near
receiver_id:  alice.near          ← account to delete
actions:
  DeleteAccount {
    beneficiary_id: alice.near    ← same as receiver_id
  }
```

**Execution trace:**

1. Transaction converts to an action receipt addressed to `alice.near`.
2. `validate_delete_action` passes — `alice.near` is a valid account ID format.
3. `action_delete_account` runs:
   - `account_balance = alice.amount()` (e.g. 10 NEAR)
   - Pushes `Receipt::new_balance_refund("alice.near", 10 NEAR)` — a system receipt.
   - Calls `remove_account(state_update, "alice.near")` — account is gone.
4. The system refund receipt is queued for `alice.near`.
5. When the refund receipt executes, `alice.near` does not exist.
6. `check_account_existence` → `Transfer` on non-existent account → `AccountDoesNotExist` error.
7. `lib.rs` line 928-932: `predecessor_id.is_system()` && `result.result.is_err()` → `other_burnt_amount += 10 NEAR`.
8. **10 NEAR is permanently burned. Alice's account is deleted. No recovery path exists.** [9](#0-8) [2](#0-1) [6](#0-5)

### Citations

**File:** runtime/runtime/src/action_validation.rs (L129-148)
```rust
fn validate_action_with_mode(
    limit_config: &LimitConfig,
    action: &Action,
    receiver: &AccountId,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ActionsValidationError> {
    match action {
        Action::CreateAccount(_) => Ok(()),
        Action::DeployContract(a) => validate_deploy_contract_action(limit_config, a),
        Action::DeployGlobalContract(a) => validate_deploy_global_contract_action(limit_config, a),
        Action::UseGlobalContract(a) => validate_use_global_contract_action(a),
        Action::FunctionCall(a) => {
            validate_function_call_action(limit_config, a, current_protocol_version, mode)
        }
        Action::Transfer(_) => Ok(()),
        Action::Stake(a) => validate_stake_action(a),
        Action::AddKey(a) => validate_add_key_action(limit_config, a, current_protocol_version),
        Action::DeleteKey(_) => Ok(()),
        Action::DeleteAccount(a) => validate_delete_action(a),
```

**File:** runtime/runtime/src/action_validation.rs (L388-392)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
```

**File:** runtime/runtime/src/actions.rs (L299-375)
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
    let account_ref = account.as_ref().unwrap();
    let account_storage_usage = if ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
        .enabled(current_protocol_version)
    {
        let contract_storage = get_contract_storage_usage(state_update, account_id, account_ref)?;
        account_ref.storage_usage().saturating_sub(contract_storage)
    } else {
        // Legacy behavior: only subtracts local contract code, misses the
        // global contract identifier overhead.
        let account_storage_usage = account_ref.storage_usage();
        let code_len = get_code_len_or_default(
            state_update,
            account_id.clone(),
            account_ref.local_contract_hash().unwrap_or_default(),
        )?;
        debug_assert!(
            code_len == 0 || account_storage_usage > code_len,
            "account storage usage should be larger than code size. storage usage: {}, code size: {}",
            account_storage_usage,
            code_len
        );
        account_storage_usage.saturating_sub(code_len)
    };
    if account_storage_usage > Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE {
        result.result =
            Err(ActionErrorKind::DeleteAccountWithLargeState { account_id: account_id.clone() }
                .into());
        return Ok(());
    }
    let gas_key_balance_to_burn = compute_gas_key_balance_sum(state_update, account_id)?;
    if gas_key_balance_to_burn > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: None,
            balance: gas_key_balance_to_burn,
        }
        .into());
        return Ok(());
    }
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
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
    Ok(())
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

**File:** integration-tests/src/user/mod.rs (L262-268)
```rust
    fn delete_account(
        &self,
        signer_id: AccountId,
        receiver_id: AccountId,
    ) -> Result<FinalExecutionOutcomeView, CommitError> {
        self.delete_account_with_beneficiary_set(signer_id.clone(), receiver_id, signer_id)
    }
```
