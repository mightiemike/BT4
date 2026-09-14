The bug-class here is "an identity used as an implicit trust anchor is not invalidated/updated when the underlying entity changes hands." I found a strong, reachable analog in NEAR's Global Contracts feature (`AccountId` deploy mode), where account-name recycling lets a new, unrelated entity take over an "owner" identity and silently redirect other accounts' contract execution.

### Title
Global-contract "AccountId" identity is not invalidated on account deletion, letting a recycled account name hijack code for all its existing users - (File: `runtime/runtime/src/global_contracts.rs`, `core/store/src/utils/mod.rs`)

### Summary
`DeployGlobalContractAction` with `GlobalContractDeployMode::AccountId` stores contract code under a `GlobalContractIdentifier::AccountId(account_id)` key, keyed purely by the account-id string, not by any persistent, non-recyclable identity of the depositor. [1](#0-0) 
Other accounts opt in via `UseGlobalContractAction`, storing `AccountContract::GlobalByAccount(account_id)` on themselves, so that every future `FunctionCall` on those accounts resolves code by re-reading whatever is currently stored at that identifier. [2](#0-1) 
`DeployGlobalContractAction`/distribution logic performs no check that the current caller is the same entity that originally deployed under that account id — it simply writes/overwrites `TrieKey::GlobalContractCode{ identifier: AccountId(account_id) }` for whoever currently controls that name. [3](#0-2) 

### Finding Description
Named NEAR accounts can be deleted (`DeleteAccountAction`) and later recreated at the same account-id (by the registrar for top-level names, or by the parent for sub-accounts). `remove_account`, the routine that deletes an account, removes the `Account` record, its contract code, access keys, gas-key nonces, and contract data — but does **not** remove any `TrieKey::GlobalContractCode`/`TrieKey::GlobalContractNonce` entries that were published under that account id via `GlobalContractDeployMode::AccountId`. [4](#0-3) 

So the "ownership" of a `GlobalContractIdentifier::AccountId(X)` slot is implicitly tied only to the account-id string `X`, exactly like the reported bug where `RdpxDecayingBonds.bonds[bondId].owner` records an owner that is never refreshed after the underlying entity (the NFT holder) changes. Here, after account `X` is deleted and recreated by a completely different, unrelated party `Z`, `Z` can submit a new `DeployGlobalContractAction { deploy_mode: AccountId }` from account `X`, which overwrites the same `GlobalContractCode` trie entry that any other account `Y` (which previously ran `UseGlobalContractAction` referencing `AccountId(X)`) is still relying on. Since resolution of `AccountContract::GlobalByAccount(X)` is done fresh on every `FunctionCall` rather than pinned to the original depositor's identity/content hash, `Y`'s contract logic is silently swapped to `Z`'s arbitrary WASM code with no action or consent from `Y`. [5](#0-4) 

### Impact Explanation
Any account `Y` that opted into `UseGlobalContractAction` with `GlobalContractIdentifier::AccountId(X)` implicitly trusts "whoever currently owns account `X`" to control its code forever, even though NEAR account ids can be deleted and recreated by an entirely different party. An attacker can grind/acquire a deletable account id `X` (or wait for one they already control to be reused), publish benign code first (or piggyback on an already-recycled name), get victims to `UseGlobalContractAction` against it, then delete and recreate `X` and redeploy malicious code — instantly hijacking the execution logic (and therefore control of funds/storage) of every account still pointing at `GlobalByAccount(X)`. This is a direct path to unauthorized value movement, since the injected code runs with the victim account's own balance and storage permissions.

### Likelihood Explanation
Reachable purely through ordinary, unprivileged transactions: `DeleteAccountAction`, `CreateAccountAction`/implicit funding, `DeployGlobalContractAction`, and `UseGlobalContractAction` are all standard actions available to any signer. No validator, network, or operator privilege is required — only account-id recycling, which is an expected, supported NEAR behavior.

### Recommendation
Bind the `AccountId`-mode global contract identity to something that cannot be silently reassigned, e.g., invalidate/clear the `GlobalContractCode`/`GlobalContractNonce` entries for `GlobalContractIdentifier::AccountId(X)` when account `X` is deleted (as part of `remove_account`), or require that redeployment under an existing `AccountId` identifier fails unless the account has never been deleted/recreated since the identifier was first established. Alternatively, deprecate the `AccountId` deploy mode's "owner can update for all its users" semantics in favor of content-addressed (`CodeHash`) identifiers only, or require explicit periodic re-confirmation by dependent accounts.

### Proof of Concept
1. Account `X` deploys a global contract with `DeployGlobalContractAction{ deploy_mode: AccountId }`, publishing benign code at `GlobalContractCode{AccountId(X)}`.
2. Account `Y` runs `UseGlobalContractAction{ contract_identifier: AccountId(X) }`, setting `AccountContract::GlobalByAccount(X)` on itself.
3. `X` self-deletes via `DeleteAccountAction`. `remove_account` clears `X`'s own account/keys/local code/data but leaves `GlobalContractCode{AccountId(X)}` untouched.
4. A different, unrelated party `Z` recreates account `X` (e.g., as its own sub-account or via the registrar/implicit path).
5. `Z`, now controlling account `X`, submits `DeployGlobalContractAction{ deploy_mode: AccountId }` with malicious code, overwriting `GlobalContractCode{AccountId(X)}`.
6. The next `FunctionCall` on `Y` resolves `AccountContract::GlobalByAccount(X)` and executes `Z`'s malicious code under `Y`'s account context, without `Y` ever re-authorizing anything — analogous to `RdpxDecayingBonds.bonds[bondId].owner` remaining stale and being trusted after the real NFT owner changed.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L76-98)
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

**File:** runtime/runtime/src/global_contracts.rs (L191-227)
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

    state_update.commit(StateChangeCause::ReceiptProcessing { receipt_hash: receipt.get_hash() });
```

**File:** core/store/src/utils/mod.rs (L504-574)
```rust
/// Removes account, code and all access keys and gas keys associated to it.
pub fn remove_account(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
) -> Result<RemoveAccountResult, StorageError> {
    state_update.remove(TrieKey::Account { account_id: account_id.clone() });
    state_update.remove(TrieKey::ContractCode { account_id: account_id.clone() });

    let mut gas_key_nonce_count: usize = 0;
    let mut gas_key_nonce_total_key_bytes: usize = 0;

    // Removing access keys and gas key nonces
    let lock = state_update.trie().lock_for_iter();
    let mut keys_to_remove: Vec<TrieKey> = Vec::new();
    for raw_key in state_update
        .locked_iter(&trie_key_parsers::get_raw_prefix_for_access_keys(account_id), &lock)?
    {
        let raw_key = raw_key?;
        let key_handle = trie_key_parsers::parse_key_handle_from_access_key_key(
            &raw_key, account_id,
        )
        .map_err(|_e| {
            StorageError::StorageInconsistentState(
                "Can't parse key handle from raw key for AccessKey".to_string(),
            )
        })?;
        let nonce_index =
            trie_key_parsers::parse_nonce_index_from_gas_key_key(&raw_key, account_id, &key_handle)
                .map_err(|_e| {
                    StorageError::StorageInconsistentState(
                        "Can't parse nonce index from raw key for AccessKey".to_string(),
                    )
                })?;
        if let Some(index) = nonce_index {
            gas_key_nonce_count += 1;
            gas_key_nonce_total_key_bytes += raw_key.len();
            keys_to_remove.push(TrieKey::gas_key_nonce(
                account_id.clone(),
                key_handle.clone(),
                index,
            ));
        } else {
            keys_to_remove.push(TrieKey::access_key(account_id.clone(), key_handle.clone()));
        }
    }
    drop(lock);

    for trie_key in keys_to_remove {
        state_update.remove(trie_key);
    }

    // Removing contract data
    let lock = state_update.trie().lock_for_iter();
    let data_keys = state_update
        .locked_iter(&trie_key_parsers::get_raw_prefix_for_contract_data(account_id, &[]), &lock)?
        .map(|raw_key| {
            trie_key_parsers::parse_data_key_from_contract_data_key(&raw_key?, account_id)
                .map_err(|_e| {
                    StorageError::StorageInconsistentState(
                        "Can't parse data key from raw key for ContractData".to_string(),
                    )
                })
                .map(Vec::from)
        })
        .collect::<Result<Vec<_>, _>>()?;
    drop(lock);

    for key in data_keys {
        state_update.remove(TrieKey::ContractData { account_id: account_id.clone(), key });
    }
    Ok(RemoveAccountResult { gas_key_nonce_count, gas_key_nonce_total_key_bytes })
```

**File:** runtime/runtime/src/lib.rs (L684-700)
```rust
            Action::FunctionCall(function_call) => {
                metrics::ACTION_CALLED_COUNT.function_call.inc();
                let account = account.as_mut().expect(EXPECT_ACCOUNT_EXISTS);
                let account_contract = account.contract().into_owned();
                let contract_id = RuntimeContractIdentifier::resolve(
                    account_id,
                    account_contract,
                    &state_update,
                    &epoch_info_provider.chain_id(),
                    AccessOptions::DEFAULT,
                )?;
                let contract = preparation_pipeline.get_contract(
                    receipt,
                    contract_id.clone(),
                    action_index,
                    None,
                );
```
