### Title
Stale `GlobalContractIdentifier::AccountId` binding survives account deletion, letting a reused account name hijack all dependent accounts' contract code - ([File: runtime/runtime/src/global_contracts.rs])

### Summary
When a global contract is deployed with `GlobalContractDeployMode::AccountId`, the resulting `GlobalContractCode` trie entry is keyed purely by the deploying account's `AccountId` string, not by any persistent identity of that account instance. `DeleteAccountAction` (`action_delete_account`) never clears this entry or its associated nonce, and any account can later be re-created under the same `AccountId` (as a fresh, unrelated account) and redeploy a global contract under `GlobalContractDeployMode::AccountId`, silently overwriting the code that every other account referencing `GlobalContractIdentifier::AccountId(that_id)` uses via `UseGlobalContractAction`. This is the same bug class as the reported "approvals persist when a token is burned and the id is reminted" issue: a delegated authorization keyed by a reusable identifier is not revoked when the underlying entity is destroyed, so a name/id reused by a new party inherits stale trust.

### Finding Description
`action_deploy_global_contract` → `initiate_distribution` builds the on-chain identifier solely from the caller's current `account_id`: [1](#0-0) 

The resulting code is written unconditionally to `TrieKey::GlobalContractCode { identifier }`, with the only "freshness" gate being a per-identifier monotonic nonce that the *same* deploy flow increments and accepts by design (`incoming_nonce >= stored_nonce`), not an ownership continuity check: [2](#0-1) [3](#0-2) 

Other accounts bind to this identifier by calling `UseGlobalContractAction`, which stores `AccountContract::GlobalByAccount(id)` on their own account, referencing the identifier by name — not by any hash or creation-height/version of the account that first deployed it: [4](#0-3) 

`action_delete_account` fully removes the account's own record, code, keys and data via `remove_account`, but this helper only clears `TrieKey::Account`, `TrieKey::ContractCode`, access/gas keys and `TrieKey::ContractData` under that account — it never touches `TrieKey::GlobalContractCode`/`TrieKey::GlobalContractNonce` for `GlobalContractIdentifier::AccountId(account_id)`: [5](#0-4) [6](#0-5) 

Consequently: deploy global contract under AccountId mode (owner "alice.near") → many accounts `UseGlobalContractAction(AccountId("alice.near"))` → "alice.near" is deleted → the same name is later created by an unrelated party (e.g., as a long top-level account permitted for anyone, per `action_create_account`'s length rule, or reused via any implicit/derived path) → the new owner deploys a different, malicious global contract with `GlobalContractDeployMode::AccountId` → every dependent account (`AccountContract::GlobalByAccount("alice.near")`) now executes the attacker's code the next time it is invoked, with zero action from those account holders. This mirrors the CouncilMember bug: the "approval" (here, the trust relationship "run whatever code account X publishes") is not revoked when account X is destroyed and its identifier is reused.

### Impact Explanation
Any account that consumed a `GlobalByAccount` reference can be silently switched to attacker-controlled WASM code without any action or consent from that account's owner, once the original publishing account is deleted and its name reused. Since global contracts are typically used for wallet/shared logic (this pattern is used for ETH-implicit wallet contracts and general shared-code deployments), an attacker who controls what code an account's `FunctionCall` receiver executes can implement arbitrary logic against that account — including logic that drains balances or bypasses expected authorization checks embedded in the contract. This is unauthorized value movement/state-transition hijacking reachable purely by ordinary `DeleteAccount`/`CreateAccount`/`DeployGlobalContract` transactions, satisfying High severity.

### Likelihood Explanation
Reaching this requires only standard actions available to any transaction signer: `DeployGlobalContractAction` (AccountId mode), `UseGlobalContractAction`, `DeleteAccountAction`, and `CreateAccountAction`. No validator, network, or privileged role is needed. The main precondition is that the original account name becomes available for re-creation (e.g., a sufficiently long top-level account, which per `action_create_account` any signer may create, or accounts under attacker-controlled parent namespaces). Given many accounts adopt the shared-owner-controlled global contract pattern specifically to let the owner "update the contract for all its users" per design intent, this is a realistic operational scenario, not a contrived edge case.

### Recommendation
Bind `GlobalContractIdentifier::AccountId` global-contract state to the account's persistent identity rather than its raw string id — e.g., invalidate/clear the corresponding `GlobalContractCode`/`GlobalContractNonce` entries in `remove_account`/`action_delete_account` when the deleted account owns an AccountId-mode global contract, or incorporate an account-creation-height/incarnation counter into the identifier's validity so a freshly (re)created account cannot silently inherit or overwrite a prior incarnation's global contract binding. At minimum, `action_delete_account` should refuse to delete (or should purge dependents' bindings) when the account has ever deployed a `GlobalContractDeployMode::AccountId` contract that is still referenced.

### Proof of Concept
1. Account `alice.near` calls `DeployGlobalContractAction{ deploy_mode: AccountId, code: codeA }` → `GlobalContractCode[AccountId("alice.near")] = codeA` (`global_contracts.rs:143-171`).
2. Accounts `bob.near`, `carol.near`, ... call `UseGlobalContractAction{ contract_identifier: AccountId("alice.near") }` → each sets `AccountContract::GlobalByAccount("alice.near")` (`global_contracts.rs:76-109`).
3. `alice.near` calls `DeleteAccountAction{ beneficiary_id: ... }`; `remove_account` clears alice's own account/keys/data but leaves `GlobalContractCode[AccountId("alice.near")]` and its nonce untouched (`core/store/src/utils/mod.rs:504-575`, `runtime/runtime/src/actions.rs:330-406`).
4. An attacker creates a new, unrelated account also named `alice.near` (permitted once available, subject to normal `action_create_account` rules).
5. The attacker (now controlling `alice.near`) calls `DeployGlobalContractAction{ deploy_mode: AccountId, code: maliciousCode }`, which overwrites `GlobalContractCode[AccountId("alice.near")]` (`global_contracts.rs:143-171`, `191-226`).
6. The next `FunctionCall` receipt to `bob.near`/`carol.near` resolves and executes `maliciousCode` via `RuntimeContractIdentifier::resolve` (`runtime/runtime/src/contract_code.rs:32-73`), completing the takeover without any action by `bob.near`/`carol.near`.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L76-109)
```rust
pub(crate) fn use_global_contract(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
    account: &mut Account,
    contract_identifier: &GlobalContractIdentifier,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let key = TrieKey::GlobalContractCode { identifier: contract_identifier.clone().into() };
    if !state_update.contains_key(&key, AccessOptions::DEFAULT)? {
        result.result = Err(ActionErrorKind::GlobalContractDoesNotExist {
            identifier: contract_identifier.clone(),
        }
        .into());
        return Ok(());
    }
    clear_account_contract_storage_usage(state_update, account_id, account)?;
    if account.contract().is_local() {
        state_update.remove(TrieKey::ContractCode { account_id: account_id.clone() });
    }
    let contract = match contract_identifier {
        GlobalContractIdentifier::CodeHash(code_hash) => AccountContract::Global(*code_hash),
        GlobalContractIdentifier::AccountId(id) => AccountContract::GlobalByAccount(id.clone()),
    };
    account.set_storage_usage(
        account.storage_usage().checked_add(contract_identifier.len() as u64).ok_or_else(|| {
            StorageError::StorageInconsistentState(format!(
                "Storage usage integer overflow for account {}",
                account_id
            ))
        })?,
    );
    account.set_contract(contract).or_inconsistent_state(account_id)?;
    Ok(())
}
```

**File:** runtime/runtime/src/global_contracts.rs (L151-158)
```rust
    let id = match deploy_mode {
        GlobalContractDeployMode::CodeHash => {
            GlobalContractIdentifier::CodeHash(hash(&contract_code))
        }
        GlobalContractDeployMode::AccountId => {
            GlobalContractIdentifier::AccountId(account_id.clone())
        }
    };
```

**File:** runtime/runtime/src/global_contracts.rs (L191-226)
```rust
fn apply_distribution_current_shard(
    receipt: &Receipt,
    global_contract_data: &GlobalContractDistributionReceipt,
    apply_state: &ApplyState,
    state_update: &mut TrieUpdate,
) -> Result<Compute, RuntimeError> {
    let identifier = match &global_contract_data.id() {
        GlobalContractIdentifier::CodeHash(hash) => GlobalContractCodeIdentifier::CodeHash(*hash),
        GlobalContractIdentifier::AccountId(account_id) => {
            GlobalContractCodeIdentifier::AccountId(account_id.clone())
        }
    };

    let is_nonce_fresh = check_and_update_nonce(global_contract_data, &identifier, state_update)?;
    if !is_nonce_fresh {
        return Ok(0);
    }

    let config = apply_state.config.wasm_config.clone();
    let trie_key = TrieKey::GlobalContractCode { identifier };
    let code_len = global_contract_data.code().len() as u64;
    state_update.set(trie_key, global_contract_data.code().to_vec());

    // Record the deploy so a same-chunk call can find the code without a warm cache.
    let code_hash = match global_contract_data.id() {
        GlobalContractIdentifier::CodeHash(hash) => Some(*hash),
        GlobalContractIdentifier::AccountId(_) => None,
    };
    if ProtocolFeature::GlobalContractSameChunkCallFix.enabled(apply_state.current_protocol_version)
    {
        state_update.record_global_contract_deploy(ContractCode::new(
            global_contract_data.code().to_vec(),
            code_hash,
        ));
    }

```

**File:** runtime/runtime/src/global_contracts.rs (L251-269)
```rust
fn check_and_update_nonce(
    global_contract_data: &GlobalContractDistributionReceipt,
    identifier: &GlobalContractCodeIdentifier,
    state_update: &mut TrieUpdate,
) -> Result<bool, RuntimeError> {
    let nonce_key = TrieKey::GlobalContractNonce { identifier: identifier.clone() };
    let stored_nonce = get_nonce(state_update, &nonce_key)?;
    let incoming_nonce = global_contract_data.nonce();

    // Allow the same nonce since the nonce is updated immediately when
    // initiating distribution to prevent multiple distributions with the same
    // nonce from being initiated.
    if incoming_nonce < stored_nonce {
        return Ok(false);
    }

    set_nonce(state_update, nonce_key, incoming_nonce);
    Ok(true)
}
```

**File:** core/store/src/utils/mod.rs (L504-510)
```rust
/// Removes account, code and all access keys and gas keys associated to it.
pub fn remove_account(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
) -> Result<RemoveAccountResult, StorageError> {
    state_update.remove(TrieKey::Account { account_id: account_id.clone() });
    state_update.remove(TrieKey::ContractCode { account_id: account_id.clone() });
```

**File:** runtime/runtime/src/actions.rs (L330-405)
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
```
