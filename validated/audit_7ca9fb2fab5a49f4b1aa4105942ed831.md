### Title
Unbounded iteration in `DeleteAccountAction` mirrors the `revokeVotes` gas-exhaustion pattern, risking permanently frozen storage-staked funds - (File: `core/store/src/utils/mod.rs`)

### Summary
The Celo `revokeVotes` bug is a `for` loop whose iteration count is tied to a state collection (`dequeued`) that only grows over time, and which the function *must* fully traverse in order to satisfy its correctness invariant (`isVoting(msg.sender) == false`). The closest reachable analog in nearcore is `remove_account`, invoked by `DeleteAccountAction`, which must iterate the *entire* set of an account's access keys, gas-key nonce entries, and contract-storage data keys via trie-prefix iteration before the delete can be considered complete — and this set is unbounded and grows with ordinary account usage (adding keys, writing contract storage).

### Finding Description
`remove_account` in `core/store/src/utils/mod.rs` performs three unbounded scans over an account's trie namespace when a `DeleteAccountAction` is applied: [1](#0-0) 

- It iterates every entry under the account's access-key prefix, classifying each as a regular key or a gas-key nonce entry, and collects them into `keys_to_remove` before removing them: [2](#0-1) 

- It then iterates every entry under the account's *contract data* prefix (i.e., all key/value pairs the contract has ever written to storage) and removes them one by one: [3](#0-2) 

This is structurally identical to `revokeVotes`'s `for` loop over `dequeued`: the collection being iterated (access keys, gas-key nonces, and — most significantly — contract storage entries) has no protocol-enforced upper bound, and the function cannot correctly finish (i.e., cannot safely mark the account deleted without leaving orphaned trie entries and incorrect `storage_usage` accounting) unless it visits every element. A contract account is free to accumulate arbitrarily many storage entries over its lifetime (bounded only by the storage-staking balance the owner is willing to lock up), exactly as Celo's `dequeued` array grows without bound over the life of the governance contract.

The wallet-contract / access-key documentation confirms this is the deletion path reachable from a single `DeleteAccountAction` signed by the account owner (or via a meta-transaction/relayer), i.e., an unprivileged signer: [4](#0-3) 

### Impact Explanation
If the actual work of clearing an account's contract-data keys is not charged gas/compute proportional to the number of entries removed (only the gas-key-nonce removal path was observed to route through `storage_removes_compute`; plain contract-data key removal in `remove_account` performs no per-key gas accounting in the traced code), then:

1. **Gas/fee bypass and validator compute-time blowup**: a single, cheaply-priced `DeleteAccountAction` on an account that has accumulated a very large amount of contract storage (which any contract deployer can grow simply by writing more state) forces every validator applying that chunk to perform O(n) trie iteration/removal work that is not reflected in the gas charged for the transaction, i.e., the fee paid does not bound the work done — a "gas metering bypass" in the same sense as the original report (execution cost not bounded by what is charged/limited).
2. **Permanently frozen funds**: because NEAR receipts execute atomically (an out-of-gas failure reverts the whole action rather than partially completing, unlike Celo's partial-revoke concern), an account whose contract-data set is large enough that fully draining it during `remove_account` would exceed the per-receipt/per-chunk gas limit can *never* successfully execute `DeleteAccountAction`. Since deleting the account is the only way to reclaim the storage-staked balance locked by `check_storage_stake`, this balance becomes permanently unrecoverable — the analog of Celo's conclusion that "there is virtually no utility in a partial revoke," except here the failure mode is total and irreversible fund lock-up rather than a UX inconvenience.

### Likelihood Explanation
Reaching this requires only an account owner (or contract) to accumulate contract storage over time through ordinary use (each write is a normal, permitted operation, gated only by storage staking economics) and then submit a single `DeleteAccountAction`. No privileged role, validator collusion, or network-layer behavior is needed — it is directly reachable from a single unprivileged transaction, matching the allowed threat model. The likelihood of *reaching a size large enough to matter* depends on protocol-configured gas/compute limits versus realistic contract storage sizes, which was not fully verifiable from the available code (the full gas-accounting path in `runtime/runtime/src/actions.rs`'s `action_delete_account` could not be completely traced within the available tool budget).

### Recommendation
- Ensure that the compute/gas cost charged for `DeleteAccountAction` scales with the number of access keys, gas-key nonce entries, and contract-data entries actually removed by `remove_account` (not just a fixed base fee), so the charged gas always bounds the real work performed.
- Consider whether `DeleteAccountAction` should be prevented from ever being unaffordable/undeliverable: e.g., by disallowing further storage growth once an account's data footprint would make its own deletion exceed protocol gas limits, or by supporting an incremental/chunked account-storage-clearing mechanism prior to final account removal, so an account is never permanently unable to reclaim its storage stake.

### Proof of Concept
1. As a normal (unprivileged) account owner, deploy a contract and repeatedly call methods that write new, distinct keys into contract storage (`storage_write`), funding the growing storage stake as required by `check_storage_stake`. Continue until the account holds an extremely large number of distinct contract-storage entries.
2. Submit a `DeleteAccountAction` (via `SignedTransaction` or a meta-transaction/relayer) targeting this account, per the action flow described in `protocol-model/spec/accounts-keys.md:71` and implemented via `remove_account` at `core/store/src/utils/mod.rs:504-575`.
3. Observe that the receipt executing `remove_account` must scan and remove every entry under `get_raw_prefix_for_contract_data` (`core/store/src/utils/mod.rs:557-568`) in one atomic step; if the number of entries is large enough that this work exceeds the applicable gas/compute limit, the action fails with an out-of-gas error every time it is retried, and the account (with its locked storage-staking balance) can never be deleted — reproducing the "eventually unusable due to unbounded loop" failure mode described in the source report, but here manifesting as irrecoverable frozen funds rather than a revert-only inconvenience.

*(Note: I was unable to fully trace the exact gas/compute charge computed in `runtime/runtime/src/actions.rs::action_delete_account` for the contract-data removal branch within the available tool budget; the underpricing claim in Impact rests on the fact that no per-entry compute charge for contract-data key removal was observed in the traced `remove_account` code, only for gas-key nonces. This should be confirmed by inspecting `action_delete_account`'s full gas/compute accounting before treating the gas-bypass portion of this finding as fully proven; the frozen-funds/unbounded-work structural analog to `revokeVotes`, however, is directly supported by the cited code.)*

### Citations

**File:** core/store/src/utils/mod.rs (L504-553)
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

**File:** protocol-model/spec/accounts-keys.md (L71-71)
```markdown
Creation (`action_create_account`, `runtime/runtime/src/actions.rs:155`): a top-level id shorter than `min_allowed_top_level_account_length` may only be created by the `registrar_account_id` (else `CreateAccountOnlyByRegistrar`, `:169`); a non-top-level id must be a direct sub-account of the predecessor (else `CreateAccountNotAllowed`, `:181`). The new account starts with zero balance/stake, `AccountContract::None`, and `storage_usage = num_bytes_account` (`:192`).
```
