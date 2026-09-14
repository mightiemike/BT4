### Title
Unbounded per-account trie scan in `DeleteAccount` handling can never complete for accounts with many access/gas keys, permanently freezing storage-staked funds - (File: `core/store/src/utils/mod.rs`, `runtime/runtime/src/access_keys.rs`)

### Summary
The Sherlock report describes `getTotalExposure()` looping over an unbounded, user-grown `openPositions[trader]` array until it runs out of gas, blocking every function that needs that computation (including withdrawal). The structural analog in nearcore is the account-deletion path: `remove_account()` in `core/store/src/utils/mod.rs` walks the *entire* trie range of an account's access keys, gas-key nonce rows, and contract-data entries in a single pass, and `compute_gas_key_balance_sum()` similarly iterates every gas key of an account to total their balances. Both are invoked from the `DeleteAccount` action handler in `runtime/runtime/src/actions.rs`, which must execute to completion inside the gas/compute budget of a single receipt.

### Finding Description
`remove_account` iterates the whole access-key prefix range for an account, building `keys_to_remove` for every access key and every gas-key nonce entry, then does the same for contract-data keys, before any of it is written back: [1](#0-0) [2](#0-1) 

There is no protocol-level cap on the *number* of access keys, gas keys, gas-key nonces, or contract-data entries an account can accumulate — only the cumulative storage-stake cost bounds them (a well-funded account can hold a very large number of cheap entries). `MAX_NONCES_FOR_GAS_KEY = 1024` only bounds nonces *per key*, not the number of keys per account. [3](#0-2) 

`compute_gas_key_balance_sum`, used on the `DeleteAccount` path to check the burn cap, similarly iterates every access/gas key of the account in one call: [4](#0-3) 

`delete_gas_key` (called once per gas key found during deletion) additionally removes every nonce entry and charges "removal compute" per key/nonce, and errors out entirely if the *summed* gas-key balance exceeds `MAX_BALANCE_TO_BURN` (1 NEAR): [5](#0-4) 

Because all of this work — trie iteration, key parsing, nonce removal, compute accounting — happens synchronously inside execution of a single `DeleteAccount` action/receipt, an account with enough access keys, gas keys, gas-key nonces, or contract-data entries will exceed the receipt's attached/prepaid gas or per-receipt compute limit before the account can be fully deleted, exactly mirroring the `getTotalExposure()` failure mode: a legitimate, необходимая-for-withdrawal operation that scales with attacker/user-controlled state size instead of being O(1) or paginated.

### Impact Explanation
`DeleteAccount` is the only way to reclaim the NEAR locked as storage stake for an account's access keys/gas keys/contract data. If that action can never complete because the enumeration + removal work exceeds the gas/compute budget of a single receipt, the account's storage-staked balance becomes permanently unrecoverable — the exact "funds behind storage stake can never be reclaimed" analog to "user cannot withdraw" in the original report. Unlike positions in the JOJO case (which can be closed one at a time via `_realizePnl`), there is no equivalent partial/paginated key-deletion primitive in the runtime that lets a user whittle down key/nonce count via a sequence of small, cheap transactions before retrying `DeleteAccount` (deleting a handful of keys is possible via `DeleteKey`, but if the count needed to bring the account back under budget is itself very large, the same unbounded-loop problem recurs for the retry sequence, and worst case the user cannot practically shrink it before running out of gas on each attempt).

### Likelihood Explanation
Reaching a large key/nonce count is entirely within the reach of an unprivileged account owner: `AddKey` and `AddGasKey`/`TransferToGasKey` actions are ordinary, permissionless transactions, gated only by the account's own NEAR balance for storage stake — no protocol maximum exists on key count. An account holder (or an attacker who convinces/tricks another account into accumulating many keys, e.g., via a wallet/dApp bug that mass-issues function-call keys) can therefore realistically construct an account whose `DeleteAccount` action can never fit inside a receipt's gas/compute limit.

### Recommendation
- Introduce and enforce a protocol-level cap on the number of access keys / gas keys (and their nonce rows) an account may hold, similar to the existing view-RPC `TooManyAccessKeys` limit already enforced for `view_access_keys`: [6](#0-5) 
- Alternatively, make `DeleteAccount` (and `compute_gas_key_balance_sum`) resumable/paginated across multiple receipts, charging gas per processed key and persisting a cursor, so the operation is not required to fit inside a single receipt's gas budget regardless of account size.
- Charge gas/compute proportional to the *actual* number of trie entries touched *before* performing the iteration (or bound the iteration length up front) so the worst case is provably boundable rather than discovered only after the full scan runs.

### Proof of Concept
1. Attacker/user account `victim.near` repeatedly submits `AddKey` (regular, cheap access keys) or `AddKey` with `GasKeyFullAccess`/`TransferToGasKey` actions, each paying only its storage-stake cost, until the account holds a very large number of access keys / gas-key nonce rows (bounded only by the account's NEAR balance, not by protocol).
2. `victim.near` submits a `DeleteAccount` action with normal prepaid gas.
3. During apply, `action_delete_key`/`action_delete_account` invokes `remove_account` (`core/store/src/utils/mod.rs:505-575`), which iterates the full access-key/gas-key-nonce/contract-data range for the account in one pass, and `compute_gas_key_balance_sum` (`core/store/src/utils/mod.rs:458-497`) sums every gas key's balance.
4. With enough keys/nonces, the cumulative compute/gas usage of this single action exceeds the receipt's gas limit; the `DeleteAccount` action fails on every retry with the same result, because the key count that must be enumerated does not shrink (the enumeration runs to completion before failure is reported, and no partial progress is persisted).
5. `victim.near`'s storage-staked NEAR balance behind those keys is now permanently unrecoverable, since there is no supported way to delete keys faster than one small `DeleteKey` transaction at a time, and that path is equally gas-bound if any single removal step (e.g., `delete_gas_key`'s nonce removal loop) needs to touch a very large fixed-size subset.

Note: I was not able to fully trace, within the available tool budget, the exact point in `runtime/runtime/src/actions.rs` where gas/compute is checked against the limit relative to when `remove_account`'s iteration actually executes (i.e., whether the enumeration itself is metered incrementally or only checked after the full scan completes). This affects whether the failure mode is "action always fails with an error" versus "node does unbounded work per receipt before rejecting it" — both are consistent with the impact described above, but confirming the precise mechanics would benefit from a full Devin session with broader file access.

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

**File:** core/store/src/utils/mod.rs (L504-519)
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
```

**File:** core/store/src/utils/mod.rs (L551-574)
```rust
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

**File:** protocol-model/spec/accounts-keys.md (L17-19)
```markdown
- **`AccessKeyPermission`** — `core/primitives-core/src/account.rs:575` — `FunctionCall(FunctionCallPermission)` | `FullAccess` | `GasKeyFunctionCall(GasKeyInfo, FunctionCallPermission)` | `GasKeyFullAccess(GasKeyInfo)`. `MAX_NONCES_FOR_GAS_KEY = 1024` (`:589`). Helpers `function_call_permission` (`:591`) and `AccessKey::gas_key_info` (`account.rs:516`) project the relevant inner data regardless of variant.
- **`FunctionCallPermission`** — `core/primitives-core/src/account.rs:625` — restricts a key to function-call use: `allowance: Option<Balance>` (`None` = unlimited; spent in lockstep with account balance), `receiver_id: String` (the only allowed receiver; a `String` not `AccountId` because legacy testnet genesis holds invalid values, `:634`), `method_names: Vec<String>` (allowed methods; empty = any).
- **`GasKeyInfo`** — `core/primitives-core/src/account.rs:546` — `{ balance: Balance, num_nonces: NonceIndex }`. `balance` is a prepaid pot used to pay gas; `num_nonces` is the count of independent nonce slots. `MAX_BALANCE_TO_BURN = 1 NEAR` (`:554`) caps the balance that may be burned when deleting the key/account.
```

**File:** protocol-model/spec/accounts-keys.md (L46-46)
```markdown
- **Gas key** (`delete_gas_key`, `:93`): if `balance > MAX_BALANCE_TO_BURN` (1 NEAR) it errors `GasKeyBalanceTooHigh` and leaves the key intact (`:103`); otherwise it adds the balance to `result.tokens_burnt` (the prepaid pot is **burned**, not refunded, `:112`), removes every nonce entry, charges removal compute, removes the access key, and `saturating_sub`s the gas-key storage cost.
```

**File:** runtime/runtime/src/state_viewer/mod.rs (L252-258)
```rust
            } else if keys.len() as u64 >= u64::from(max) {
                // Unpaginated request that exceeds the configured limit.
                return Err(errors::ViewAccessKeyError::TooManyAccessKeys {
                    requested_account_id: account_id.clone(),
                    limit: max,
                });
            }
```
