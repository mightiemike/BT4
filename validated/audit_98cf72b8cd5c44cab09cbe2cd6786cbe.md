### Title
Denial of Service via Unmetered Linear Scan in `DeleteAccount`'s Gas-Key Balance Sum - (File: `core/store/src/utils/mod.rs`)

### Summary
`compute_gas_key_balance_sum`, invoked by `action_delete_account`, performs an unbounded, effectively unmetered linear scan over every trie row under an account's access-key prefix — including every individual gas-key nonce row — before any compute cost is charged for that scan. An attacker can inflate the number of rows scanned almost for free (gas keys support up to `MAX_NONCES_FOR_GAS_KEY = 1024` nonce rows each), then trigger the scan with a single `DeleteAccount` action whose flat fee does not scale with the number of keys/nonces, producing disproportionate, unpriced validator CPU work per transaction — the same class of bug as the audited Move `teleport::teleport_from_flow` issue (an O(n) structure scanned on every call, with n attacker-controlled and not priced into gas).

### Finding Description
`compute_gas_key_balance_sum` iterates the whole access-key key-range of an account to sum gas-key balances: [1](#0-0) 

Unlike `TrieViewer::view_access_keys`, which explicitly skips a gas key's nonce-row block with a `prefix_successor` seek to avoid visiting every nonce entry: [2](#0-1) 

`compute_gas_key_balance_sum` has no such skip — it just `continue`s for every nonce row it encounters, meaning the trie iterator must still advance through and parse every single nonce row one at a time: [3](#0-2) 

This function is called from `action_delete_account`, the handler for the `DeleteAccount` action, before any compute is charged for the account-deletion path: [4](#0-3) 

The only compute charge associated with account deletion is `storage_removes_compute`, applied later and scoped to `gas_key_nonce_count`/byte totals for the *removal* writes performed by `remove_account`: [5](#0-4) 

That charge is a separate operation from, and does not compensate for, the earlier read-heavy scan in `compute_gas_key_balance_sum`. Each gas key can carry up to 1024 nonce rows: [6](#0-5) 

so an attacker who funds many gas keys, each with the maximum nonce count, can multiply the number of trie rows `compute_gas_key_balance_sum` must visit far beyond what the flat `DeleteAccount` action fee or the subsequent removal-compute charge accounts for.

### Impact Explanation
Because the row-scanning cost of `compute_gas_key_balance_sum` is not proportional to any gas/compute charged for the `DeleteAccount` action, a single transaction can force validators to perform an outsized amount of trie-read work relative to the gas paid for it. This is a gas-metering bypass: the protocol's gas accounting is supposed to bound the compute time any single transaction can force onto block/chunk producers, and this path escapes that bound. At sufficient scale (attacker funds many gas keys × 1024 nonces before issuing one `DeleteAccount`), this can materially slow chunk application relative to the gas charged, which is the resource-exhaustion/DoS pattern the reference report identifies (an uncharged O(n) scan keyed by attacker-controlled state).

### Likelihood Explanation
The storage cost to create many access/gas keys and nonce rows is standard NEAR storage staking, and that NEAR is recoverable: `action_delete_account` refunds the account's remaining balance to the beneficiary once the account is deleted, and only the (separately capped) gas-key balance is burned, not the storage stake itself. This makes the setup cost largely recoverable, and the trigger is a single, ordinary `DeleteAccount` transaction reachable by any account owner — no validator or privileged role is required.

### Recommendation
Charge compute proportional to the number of rows/bytes visited by `compute_gas_key_balance_sum` (mirroring the accounting already done for the removal phase), and/or make the scan skip nonce-row blocks the same way `view_access_keys` does (seek past a gas key's nonce range instead of visiting each nonce row), so the per-row work performed before removal is both bounded and metered.

### Proof of Concept
1. Attacker account creates a large number of gas keys via `AddKey` with `GasKeyFullAccess`/`GasKeyFunctionCall` permission, each configured with `num_nonces = MAX_NONCES_FOR_GAS_KEY (1024)`, funding enough storage stake to cover the keys and their nonce rows (recoverable on deletion).
2. Attacker submits a single `DeleteAccount` action for that account.
3. During apply, `action_delete_account` calls `compute_gas_key_balance_sum`, which must iterate every access-key-prefixed trie row — including all `keys × 1024` nonce rows — one at a time (no skip-ahead), before the removal/compute-charging phase runs.
4. The gas/compute charged for the `DeleteAccount` action and the later `storage_removes_compute` call do not scale to cover this initial scan, so the validator performs disproportionate uncharged work for the fee paid, and the attacker recovers most/all of the staked NEAR via the balance-refund receipt issued in the same action.

### Citations

**File:** core/store/src/utils/mod.rs (L457-497)
```rust
/// Computes the total balance across all gas keys for a given account.
pub fn compute_gas_key_balance_sum(
    state_update: &TrieUpdate,
    account_id: &AccountId,
) -> Result<Balance, StorageError> {
    let mut total = Balance::ZERO;
    let lock = state_update.trie().lock_for_iter();
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
        if nonce_index.is_some() {
            continue;
        }
        if let Some(balance) = get_access_key_by_handle(state_update, account_id, &key_handle)?
            .as_ref()
            .and_then(|access_key| access_key.gas_key_info())
            .map(|gas_key_info| gas_key_info.balance)
        {
            total = total.checked_add(balance).ok_or_else(|| {
                StorageError::StorageInconsistentState("gas key balance overflow".to_string())
            })?;
        }
    }
    Ok(total)
}
```

**File:** runtime/runtime/src/state_viewer/mod.rs (L265-273)
```rust
            // A gas key's nonce rows sort immediately after its access-key row;
            // skip the whole block so we don't scan every nonce.
            let is_gas_key = access_key.gas_key_info().is_some();
            keys.push((key_handle, access_key));
            if is_gas_key {
                if let Some(next) = prefix_successor(&raw_key) {
                    iter.seek(Bound::Included(next.as_slice()))?;
                }
            }
```

**File:** runtime/runtime/src/actions.rs (L370-379)
```rust
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
```

**File:** runtime/runtime/src/actions.rs (L392-402)
```rust
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

**File:** protocol-model/spec/accounts-keys.md (L17-17)
```markdown
- **`AccessKeyPermission`** — `core/primitives-core/src/account.rs:575` — `FunctionCall(FunctionCallPermission)` | `FullAccess` | `GasKeyFunctionCall(GasKeyInfo, FunctionCallPermission)` | `GasKeyFullAccess(GasKeyInfo)`. `MAX_NONCES_FOR_GAS_KEY = 1024` (`:589`). Helpers `function_call_permission` (`:591`) and `AccessKey::gas_key_info` (`account.rs:516`) project the relevant inner data regardless of variant.
```
