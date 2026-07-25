### Title
`DeleteAccountAction` Allows `beneficiary_id == account_id`, Permanently Burning the Deleted Account's Balance - (File: `runtime/runtime/src/actions.rs`)

### Summary

`action_delete_account` creates a `Receipt::new_balance_refund` targeting `delete_account.beneficiary_id` before calling `remove_account`. No validation prevents `beneficiary_id` from equalling the receipt's `receiver_id` (the account being deleted). When the refund receipt later arrives at the now-deleted account, the Transfer fails, and because the receipt carries `predecessor_id = "system"`, the runtime burns the deposit instead of refunding it. The entire account balance is permanently destroyed.

### Finding Description

`action_delete_account` in `runtime/runtime/src/actions.rs` executes in this order:

1. Reads `account_balance` from the account being deleted.
2. Pushes `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)` onto `result.new_receipts`.
3. Calls `remove_account(state_update, account_id)`, erasing the account from the trie. [1](#0-0) 

The only validation applied to `beneficiary_id` before execution is `validate_delete_action`, which only checks that the string is a syntactically valid account ID: [2](#0-1) 

There is no check that `beneficiary_id != receipt.receiver_id()` (the account being deleted).

`Receipt::new_balance_refund` always sets `predecessor_id = "system"`: [3](#0-2) 

When this system receipt is later processed and the receiver account no longer exists (because it was just deleted), the Transfer action fails with `AccountDoesNotExist`. The runtime's refund path explicitly burns the deposit of any failed system receipt: [4](#0-3) 

### Impact Explanation

Any user who submits a `DeleteAccount { beneficiary_id: <own_account_id> }` transaction loses their entire liquid account balance permanently. The tokens are added to `stats.balance.other_burnt_amount` and subtracted from total supply — they are unrecoverable. This is a direct, irreversible loss of funds triggered by a single unprivileged user transaction.

For named (non-implicit) accounts this is unconditional: the deleted account cannot be re-created by a system Transfer, so the Transfer fails and the balance burns. For 64-char hex implicit accounts the Transfer would re-create the account, but the original access keys were removed by `remove_account`, so the user may still lose access depending on whether they retain the corresponding private key.

### Likelihood Explanation

The trigger is a single, syntactically valid transaction that passes all existing validation gates. A buggy contract calling `promise_batch_action_delete_account` with `env::current_account_id()` as the beneficiary (a natural mistake when a contract deletes itself and intends to refund itself) would silently burn its entire balance. A user constructing a raw transaction with the wrong beneficiary field would have the same outcome. There is no warning, no error at submission time, and no recovery path. [5](#0-4) 

### Recommendation

Add a check in `validate_delete_action` (or at the start of `action_delete_account`) that rejects the action when `beneficiary_id == account_id` (the receipt receiver). This mirrors the fix applied to the PSP22Wrapper analog: reject the self-referential parameter before any state mutation occurs.

```rust
fn validate_delete_action(
    action: &DeleteAccountAction,
    receiver: &AccountId,
) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;
    if &action.beneficiary_id == receiver {
        return Err(ActionsValidationError::DeleteAccountBeneficiaryIsSelf);
    }
    Ok(())
}
```

`validate_action_with_mode` already receives `receiver` and passes it to other validators (e.g., `validate_deterministic_state_init`), so threading it into `validate_delete_action` requires only a minor signature change. [6](#0-5) 

### Proof of Concept

```
1. Alice holds account "alice.near" with 100 NEAR balance.
2. Alice submits:
     Transaction {
       signer_id:   "alice.near",
       receiver_id: "alice.near",
       actions: [DeleteAccount { beneficiary_id: "alice.near" }]
     }
3. Validation passes: "alice.near" is a valid account ID.
4. action_delete_account executes:
   a. account_balance = 100 NEAR
   b. new_receipts.push(Receipt::new_balance_refund("alice.near", 100 NEAR))
      → predecessor_id = "system", receiver_id = "alice.near"
   c. remove_account("alice.near")  ← account deleted from trie
5. The balance-refund receipt is processed in a subsequent chunk:
   a. receiver "alice.near" does not exist → Transfer fails (AccountDoesNotExist)
   b. predecessor_id.is_system() == true && result.is_err() == true
      → other_burnt_amount += 100 NEAR
6. Alice's 100 NEAR is permanently burned. Total supply decreases by 100 NEAR.
``` [7](#0-6) [8](#0-7)

### Citations

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

**File:** runtime/runtime/src/action_validation.rs (L127-148)
```rust
/// Validates a single given action.
/// The `mode` only affects nested validation of `Action::Delegate` payloads
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

**File:** core/primitives/src/receipt.rs (L428-430)
```rust
    pub fn balance_refund_receiver(&self) -> &AccountId {
        self.refund_to().as_ref().unwrap_or_else(|| self.predecessor_id())
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

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3492-3512)
```rust
    pub fn promise_batch_action_delete_account(
        &mut self,
        promise_idx: u64,
        beneficiary_id_len: u64,
        beneficiary_id_ptr: u64,
    ) -> Result<()> {
        self.result_state.gas_counter.pay_base(base)?;
        if self.context.is_view() {
            return Err(HostError::ProhibitedInView {
                method_name: "promise_batch_action_delete_account".to_string(),
            }
            .into());
        }
        let beneficiary_id =
            self.read_and_parse_account_id(beneficiary_id_ptr, beneficiary_id_len)?;

        let (receipt_idx, sir) = self.promise_idx_to_receipt_idx_with_sir(promise_idx)?;
        self.pay_action_base(ActionCosts::delete_account, sir)?;

        self.ext.append_action_delete_account(receipt_idx, beneficiary_id);
        Ok(())
```
