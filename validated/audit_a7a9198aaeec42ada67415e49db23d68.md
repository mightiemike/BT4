## Title
Compute/gas metering for `DeleteAccount` does not charge for removing regular access keys or contract data, allowing unmetered work during account deletion - (File: `core/store/src/utils/mod.rs`, `runtime/runtime/src/actions.rs`)

### Summary
`remove_account` iterates over and removes **all** access-key trie entries and **all** contract-data trie entries belonging to a deleted account, but `action_delete_account` only converts a subset of that work (gas-key nonce removals) into billed `compute_usage`. The base access-key entries (including regular `FullAccess`/`FunctionCall` keys and each gas key's own access-key record) and every contract-data key/value pair are removed for free, with no corresponding compute charge — mirroring the reported bug class of state that is "pushed" (via ordinary paid `AddKey`/`storage_write` calls across many transactions) but never billed for when it is later "popped" and iterated in one shot.

### Finding Description
`remove_account` (`core/store/src/utils/mod.rs:504-575`) walks the account's access-key prefix and its contract-data prefix and removes every entry found: [1](#0-0) [2](#0-1) 

Only the gas-key **nonce** rows are counted into `RemoveAccountResult { gas_key_nonce_count, gas_key_nonce_total_key_bytes }`; the base `TrieKey::access_key(...)` entries pushed for every regular key (and every gas key's own record) are removed via `state_update.remove(trie_key)` with no counting, and the entire contract-data loop removes keys with no counting at all: [3](#0-2) 

In `action_delete_account` (`runtime/runtime/src/actions.rs:330-406`), the only compute charge derived from `remove_account`'s output is for gas-key nonces: [4](#0-3) 

Compare with `delete_gas_key` (`runtime/runtime/src/access_keys.rs:93-134`), which correctly charges `storage_removes_compute` for the nonces it removes, but that function only ever deletes one gas key's nonces at a time — it does not cover the bulk multi-key case that `remove_account` performs. [5](#0-4) 

The only guard against an attacker inflating this unbilled work is the pre-check on `account_storage_usage` against `Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE`: [6](#0-5) 
This check bounds total storage bytes recorded on the account (which does include access-key and contract-data storage-usage accounting elsewhere in the codebase), so the number of trie entries removable in one `DeleteAccount` action is bounded by that storage-usage limit divided by average entry size — not literally unbounded. However, within that permitted storage-usage budget, an attacker can hold many small access keys and/or many small contract-data entries (each individually paid for at creation via `AddKey`/`storage_write` fees), then trigger their bulk, iterated removal in a single `DeleteAccount` receipt whose `compute_usage`/`gas_burnt` reflects only the fixed `delete_account_cost` base fee plus (if any) gas-key-nonce compute — not the proportional `storage_remove_base`/`storage_remove_key_byte` compute that an equivalent number of explicit `storage_remove` host calls would cost (as seen for the correctly-metered contract-call path in `runtime/near-vm-runner/src/wasmtime_runner/logic.rs:5032-5061`).

### Impact Explanation
This is a gas/compute metering bypass: the `DeleteAccount` action performs proportional-to-N trie removal work (N = number of access keys + contract-data entries) while being billed a constant, N-independent fee. Because the compute budget (`total.compute >= compute_limit`) is the mechanism the runtime uses to bound per-chunk wall-clock work and to decide when receipts must be deferred to the delayed queue (`protocol-model/spec/runtime-execution.md:106-108`), a receipt whose real work is not reflected in `compute_usage` can consume more wall-clock/trie-removal work per chunk than the compute accounting assumes, undermining the very mechanism designed to keep per-chunk execution time bounded and deterministic across nodes. This is a fee/gas-bypass class issue (explicitly an accepted impact category), even though the storage-usage cap limits its magnitude to `MAX_ACCOUNT_DELETION_STORAGE_USAGE`-sized batches rather than truly unbounded growth.

### Likelihood Explanation
Reachable directly by any account owner: an attacker only needs to (a) add many `AddKey` actions (or `storage_write`s) to their own account up to the storage-usage cap, each paying normal fees, then (b) submit a single `DeleteAccount` action. No privileged role is required, and it can be repeated across many attacker-controlled accounts by any transaction signer. The severity is moderated because `MAX_ACCOUNT_DELETION_STORAGE_USAGE` caps the batch size per account and the un-metered discrepancy is only the *difference* between the flat `delete_account_cost` and the true proportional removal cost, not fully free unbounded computation.

### Recommendation
In `remove_account`, also count and return the number/total-key-bytes (and value-bytes where available) of the regular access-key entries and contract-data entries removed, not just gas-key nonces. In `action_delete_account`, add `storage_removes_compute` (and corresponding gas, if these removals should also burn gas rather than only compute) for all three categories: access keys, gas-key nonces, and contract-data entries, so the billed compute/gas scales with the actual number of trie writes performed, consistent with how `storage_remove` is metered for contract calls.

### Proof of Concept
1. Create account `attacker.near`.
2. Add access keys up to just under `Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE` bytes of storage usage (e.g., many `FunctionCall` keys with short method-name lists, or many small `storage_write` contract-data entries), each paid for by its own `AddKey`/`FunctionCall` transaction.
3. Submit a single `DeleteAccountAction` for `attacker.near`.
4. Observe (via `runtime/runtime/src/actions.rs::action_delete_account` and `RemoveAccountResult`) that `result.compute_usage` only reflects `gas_key_nonce_count`-derived compute (zero if no gas keys were used) plus the flat `delete_account_cost`, while `remove_account` (`core/store/src/utils/mod.rs:504-575`) performs one `state_update.remove` per access key and per contract-data entry — work whose cost is not reflected in the charged compute/gas for the receipt.

### Citations

**File:** core/store/src/utils/mod.rs (L499-502)
```rust
pub struct RemoveAccountResult {
    pub gas_key_nonce_count: usize,
    pub gas_key_nonce_total_key_bytes: usize, // used to calculate compute cost
}
```

**File:** core/store/src/utils/mod.rs (L515-553)
```rust
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
```

**File:** core/store/src/utils/mod.rs (L555-574)
```rust
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

**File:** runtime/runtime/src/actions.rs (L342-369)
```rust
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
```

**File:** runtime/runtime/src/actions.rs (L387-402)
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
```

**File:** runtime/runtime/src/access_keys.rs (L112-126)
```rust
    result.tokens_burnt =
        result.tokens_burnt.checked_add(gas_key_info.balance).ok_or(IntegerOverflowError)?;
    let num_nonces = gas_key_info.num_nonces as usize;
    for i in 0..gas_key_info.num_nonces {
        remove_gas_key_nonce(state_update, account_id.clone(), public_key.clone(), i);
    }
    let nonce_key_len = gas_key_nonce_key_len(account_id, &public_key.into());
    let nonce_remove_compute = storage_removes_compute(
        &config.wasm_config.ext_costs,
        num_nonces,
        nonce_key_len * num_nonces,
        AccessKey::NONCE_VALUE_LEN * num_nonces,
    );
    result.compute_usage = safe_add_compute(result.compute_usage, nonce_remove_compute)?;
    remove_access_key(state_update, account_id.clone(), public_key.clone());
```
