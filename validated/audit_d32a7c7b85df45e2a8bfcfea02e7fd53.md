## Title
Unbounded, Gas-Unmetered Trie Scan of All Access Keys in `DeleteAccount` (`compute_gas_key_balance_sum`) - ([File: core/store/src/utils/mod.rs])

### Summary
`action_delete_account` charges only the flat `delete_account_cost` base fee for the `DeleteAccount` action, but before deleting the account it unconditionally calls `compute_gas_key_balance_sum`, which iterates over *every* access-key trie entry belonging to the account (including every gas-key nonce row) with no gas metering tied to the number of entries scanned. An account owner can grow the number of access keys on their own account across many cheap `AddKey` transactions and then submit a single `DeleteAccount` action whose actual execution cost (trie iteration + per-key deserialization) is far larger than the flat fee charged for it.

### Finding Description
`action_delete_account` calls `compute_gas_key_balance_sum(state_update, account_id)` unconditionally, before any per-item gas is charged for that specific work: [1](#0-0) 

That function walks the full access-key-prefix range for the account via `locked_iter`, parsing and fetching every access key (and skipping only gas-key nonce rows) to sum gas-key balances: [2](#0-1) 

The only gas/compute charged in `action_delete_account` for this deletion is `storage_removes_compute`, and that is computed strictly from `remove_result.gas_key_nonce_count` — i.e., only proportional to the number of *gas-key nonce* rows removed, not to the total number of access keys (or gas keys without many nonces) that `compute_gas_key_balance_sum` had to scan: [3](#0-2) 

For comparison, the equivalent gas-key-nonce removal in `delete_gas_key` (single `DeleteKey` action path) *does* charge `storage_removes_compute` proportional to `num_nonces` for its own loop: [4](#0-3) 

But `compute_gas_key_balance_sum`'s access-key-prefix scan over the whole account (potentially thousands of regular `FullAccess`/`FunctionCall` keys with no gas-key info, which get read and discarded one by one) has no analogous charge in `action_delete_account`. The action's exec fee is the fixed `delete_account_cost` from `RuntimeFeeConfig` — a constant, not a function of how many access keys the account holds.

This mirrors the reported bug class: an unbounded loop whose cost scales with attacker-controlled state size but is not billed proportionally, so the fixed fee charged does not cover the real work performed.

### Impact Explanation
The number of access keys per account is not capped by a hard protocol limit in the codebase found (only add-key method-name-byte limits and per-key add costs were found; I did not find a `max_number_of_access_keys` limit enforced in state, only view-RPC pagination limits `access_keys_limit` used by `view_access_keys` for RPC listing, which is unrelated to write-path caps). Regular `AddKey` actions are individually charged (`add_key_cost`), so building up N access keys costs the attacker gas proportional to N across many transactions — that part is fairly priced. The issue is narrower: at `DeleteAccount` time, the *scan* cost is not billed at all, so a single `DeleteAccount` receipt can force the validating node to do O(N) trie reads/parses for a fee that is O(1). Repeating this pattern at scale (many accounts with many access keys, all deleted in the same chunk) could push actual chunk-apply CPU/IO time well beyond what gas accounting assumes, without the chunk's gas/compute budget reflecting it — a state-transition cost-estimation gap that can degrade chunk-processing throughput for honest validators. I was not able to fully verify from the index whether an explicit account-wide access-key count limit exists elsewhere in the runtime config that would cap N; if such a limit exists and is small, the severity of this specific analog is reduced to a bounded/low-severity issue rather than a true unbounded-loop DoS.

### Likelihood Explanation
Reachable via a single unprivileged transaction sequence: any account owner can (a) call `AddKey` repeatedly to accumulate many access keys on their own account (a legitimate operation costing normal gas each time), then (b) submit one `DeleteAccount` action. No special privilege, validator role, or malicious peer/node behavior is required — only ordinary transaction/action submission by the account owner.

### Recommendation
- Charge a per-scanned-key (or per-byte) compute/gas cost inside `compute_gas_key_balance_sum` (or before calling it in `action_delete_account`), proportional to the number of access-key rows actually iterated, mirroring the `storage_removes_compute` charge already applied to gas-key nonce removal.
- Alternatively, cap the number of access keys allowed per account (enforced at `AddKey` time) tightly enough that the worst-case `DeleteAccount` scan cost is bounded and already covered by `delete_account_cost`.
- Add an estimator/benchmark case for `DeleteAccount` on an account with the maximum allowed access-key count to validate the fee covers the real cost, similar to existing estimator functions for `action_add_function_access_key_*`.

### Proof of Concept
1. From a controlled account `victim.near`, submit N `AddKey` transactions (e.g., N = tens of thousands, each with a `FullAccess` or minimal `FunctionCall` permission), each individually gas-priced by `add_key_cost` — this is normal, allowed behavior.
2. Submit a single `DeleteAccount` action on `victim.near`.
3. During `apply_action` → `action_delete_account`, `compute_gas_key_balance_sum` iterates and reads all N access-key trie entries via `locked_iter`/`get_access_key_by_handle` [5](#0-4) 
 while the action is billed only the fixed `delete_account_cost` and, per [3](#0-2) 
, compute proportional only to `gas_key_nonce_count` (zero if the N keys are not gas keys) — meaning the O(N) scan work is essentially unbilled.

### Citations

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

**File:** runtime/runtime/src/access_keys.rs (L112-125)
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
```
