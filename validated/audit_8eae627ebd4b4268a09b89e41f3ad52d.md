### Title
Quadratic-cost linear scan in Starknet OS alias replacement enables single-block DoS of block proving - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo)

### Summary
The Starknet OS's stateful-compression "alias" mechanism resolves each modified storage key/contract address to a compact alias by calling `get_alias_of_big_key`, which performs a **linear** `find_element` scan over the full set of aliases touched in the block [1](#0-0) . Because this lookup is repeated once per modified key in `replace_contract_state_diff`/`replace_storage_diff_big_keys` [2](#0-1) , the total re-execution cost of the replacement phase is **O(n²)** in the number of distinct newly-aliased keys touched in a single block, mirroring the exact bug class in the report (per-array-membership scan cost multiplying with the number of packed entries).

### Finding Description
`allocate_aliases_in_storage` (blockifier side) and its Cairo counterpart `allocate_aliases`/`replace_aliases_and_serialize_full_contract_state_diff` (Starknet OS side) build one combined `Aliases` struct containing **every** distinct storage key / contract address in the block's state diff that requires compression (`key >= MIN_VALUE_FOR_ALIAS_ALLOC` and `address > MAX_NON_COMPRESSED_CONTRACT_ADDRESS`) [3](#0-2) .

During the replacement phase, for every one of those keys the OS calls `get_alias`/`get_alias_of_big_key`, which does not binary-search a sorted array or use a dictionary lookup — it calls the generic Cairo `find_element` primitive with `n_elms=aliases.len`, i.e., a **linear scan** through the whole block's alias set for each individual key being replaced [1](#0-0) . This is invoked once per storage diff entry across all touched contracts [2](#0-1)  and once per touched contract address [4](#0-3) .

The allocation phase is analogous: it walks the same growing per-block key set to allocate new aliases, and the whole per-block alias set is proportional to `state_diff_size`, one of the block's bounded resources tracked by the bouncer [5](#0-4) . Crucially, `state_diff_size` is charged/limited **linearly** (as a count of modified cells), while the actual Cairo-step cost of resolving aliases for those cells during OS re-execution grows **quadratically** with the same count. Any unprivileged sender can trivially maximize the number of distinct newly-touched, never-before-aliased storage keys within a single block/proposal (e.g., writing to a unique storage slot in many different contracts, or many storage slots via `SSTORE`-equivalent Starknet syscalls) up to the bouncer's `state_diff_size` cap, without needing any privileged role — this is a plain unprivileged transaction path (blockifier execution → syscalls → state diff → bouncer accounting → OS re-execution).

This is the direct analog of the reported bug: the RealWagmi contract paid for adding one array entry (O(1) fee) but imposed O(n) scan cost on every future interaction with the shared array; here, a sender pays fee for `state_diff_size` (O(1) per cell, linearly metered) but imposes O(n) per-key alias-lookup cost that is not linearly charged, and the aggregate re-execution/proving cost is O(n²), not O(n).

### Impact Explanation
Unlike the fee-charged blockifier execution of a transaction, the Starknet OS re-execution (used for proving/committing the block) is not gas-metered per-transaction in the same way — its resource budget is a fixed Cairo-step/proving-time budget for the whole block. An attacker who fills a block with many transactions that each touch new, distinct compressible storage keys can force the OS's alias-replacement pass into O(n²) Cairo steps for that block, disproportionate to the linear resources actually paid for (`state_diff_size`). If `n` (bounded by the bouncer's `state_diff_size` capacity) is large enough, this can push the OS execution past its available step budget or make proving prohibitively slow/expensive for that block, causing the block to fail to be proven — a network-level "unable to confirm new transactions" condition for that block, and a real proving-cost/DoS asymmetry favoring the attacker (who pays only linear fees while imposing quadratic proving cost).

### Likelihood Explanation
The path is fully reachable by an ordinary unprivileged sender: any set of transactions that touch many unique, previously-unaliased storage cells/contract addresses in one block triggers this. No special privileges, malicious operator/proposer behavior, or network-level attack is required — only ordinary transaction submission maximizing distinct new storage writes, gated only by the existing (linear) bouncer `state_diff_size` cap, which does not account for the quadratic OS-side cost.

### Recommendation
Replace the linear `find_element` scan in `get_alias_of_big_key` with an O(log n) or O(1) lookup: either (a) keep the alias entries sorted and use Cairo's `find_element`'s binary-search mode (guaranteeing sortedness and using `search_sorted`/binary variant instead of the default linear scan), or (b) use a squashed dict access pattern (as already used for `aliases_storage_updates`) so that each replacement is a direct dict read rather than a linear scan over the full block alias set. Additionally, ensure the bouncer's cost model for `state_diff_size` (or an added dedicated resource) reflects the true (at least O(n log n)) cost of alias allocation/replacement so that the metered resource scales with the real OS re-execution cost, preventing an attacker from exploiting the linear/quadratic cost mismatch.

### Proof of Concept
1. An attacker submits (or fills a block with) transactions that each write to a unique, never-before-touched storage slot in a unique contract address `> MAX_NON_COMPRESSED_CONTRACT_ADDRESS` with key `>= MIN_VALUE_FOR_ALIAS_ALLOC`, so every write requires alias allocation [6](#0-5) .
2. The attacker maximizes the number of such distinct keys up to the bouncer's `state_diff_size` cap for the block (paying only the linear per-cell fee already required for the state diff).
3. At block finalization, `allocate_aliases_in_storage` allocates aliases for all `n` keys [3](#0-2) .
4. During Starknet OS re-execution/proving, `replace_aliases_and_serialize_full_contract_state_diff` → `replace_contract_state_diff` → `replace_storage_diff_big_keys` calls `get_alias_of_big_key` for each of the `n` keys, each performing an O(n) `find_element` scan over the same `n`-sized alias array [7](#0-6) , yielding O(n²) total steps for the replacement phase alone — cost the attacker did not pay for proportionally.

**Uncertainty / what remains unverified:** I could not confirm within available context (a) the exact numeric cap on `state_diff_size` in the bouncer config nor (b) whether `find_element`'s underlying hint implementation ever falls back to a genuinely O(1)/O(log n) path (e.g., via a sorted-array optimistic hint) in all cases used here — the hint file `find_element.rs` shows an "optimistic" linear-scan hint (`SEARCH_SORTED_OPTIMISTIC`), but I did not verify whether the aliases array is guaranteed sorted or whether a faster binary-search hint variant is actually invoked for `get_alias_of_big_key` versus the plain `find_element`. This affects whether the true asymptotic cost is O(n²) (linear scan) or closer to O(n log n) (binary search), and thus the practical severity/exploitability threshold. A Devin session with full repo access and the ability to trace the compiled hint dispatch for `find_element` in `aliases.cairo` would be needed to conclusively confirm the exact algorithmic complexity and derive concrete step-count/cost numbers for a specific block size.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo (L316-317)
```text
    // Replace the contract address.
    let address_alias = get_alias(aliases=aliases, key=contract_address);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo (L360-410)
```text
// Same as `replace_storage_diff`, but assumes all keys are at least MIN_VALUE_FOR_ALIAS_ALLOC.
func replace_storage_diff_big_keys{range_check_ptr, res: felt*}(
    aliases: Aliases,
    storage_diff_start: FullStateUpdateEntry*,
    storage_diff_end: FullStateUpdateEntry*,
) {
    if (storage_diff_start == storage_diff_end) {
        return ();
    }

    let current_entry = storage_diff_start[0];
    let key_alias = get_alias_of_big_key(aliases=aliases, key=current_entry.key);
    let replaced_entry = cast(res, FullStateUpdateEntry*);
    assert [replaced_entry] = FullStateUpdateEntry(
        key=key_alias, prev_value=current_entry.prev_value, new_value=current_entry.new_value
    );
    let res = &res[FullStateUpdateEntry.SIZE];
    return replace_storage_diff_big_keys(
        aliases=aliases,
        storage_diff_start=&storage_diff_start[1],
        storage_diff_end=storage_diff_end,
    );
}

// Returns the alias of the given key.
func get_alias{range_check_ptr}(aliases: Aliases, key: felt) -> felt {
    alloc_locals;
    local key_lt_min_alias_alloc_value;
    %{ KeyLtMinAliasAllocValue %}
    if (key_lt_min_alias_alloc_value != FALSE) {
        // The alias is the key itself.
        assert_nn_le(a=key, b=MIN_VALUE_FOR_ALIAS_ALLOC - 1);
        return key;
    }

    // Verify that key >= MIN_VALUE_FOR_ALIAS_ALLOC.
    assert_le_felt(a=MIN_VALUE_FOR_ALIAS_ALLOC, b=key);
    return get_alias_of_big_key(aliases=aliases, key=key);
}

// Returns the alias of the given key.
// Assumes the given key is at least MIN_VALUE_FOR_ALIAS_ALLOC.
func get_alias_of_big_key{range_check_ptr}(aliases: Aliases, key: felt) -> felt {
    // Sanity check.
    %{ AssertKeyBigEnoughForAlias %}
    static_assert DictAccess.key == 0;
    let (entry: DictAccess*) = find_element(
        array_ptr=aliases.ptr, elm_size=DictAccess.SIZE, n_elms=aliases.len, key=key
    );
    return entry.new_value;
}
```

**File:** crates/blockifier/src/state/stateful_compression.rs (L46-80)
```rust
/// Allocates aliases for the new addresses and storage keys in the alias contract.
/// Iterates over the addresses in ascending order. For each address, sets an alias for the new
/// storage keys (in ascending order) and for the address itself.
pub fn allocate_aliases_in_storage<S: StateReader>(
    state: &mut CachedState<S>,
    alias_contract_address: ContractAddress,
) -> StateResult<()> {
    let state_diff = state.to_state_diff()?.state_maps;

    // Collect the contract addresses and the storage keys that need aliases.
    let contract_addresses: BTreeSet<ContractAddress> =
        state_diff.get_contract_addresses().into_iter().collect();
    let mut contract_address_to_sorted_storage_keys = HashMap::new();
    for (contract_address, storage_key) in state_diff.storage.keys() {
        if contract_address > &MAX_NON_COMPRESSED_CONTRACT_ADDRESS {
            contract_address_to_sorted_storage_keys
                .entry(contract_address)
                .or_insert_with(BTreeSet::new)
                .insert(storage_key);
        }
    }

    // Iterate over the addresses and the storage keys and update the aliases.
    let mut alias_updater = AliasUpdater::new(state, alias_contract_address)?;
    for contract_address in contract_addresses {
        if let Some(storage_keys) = contract_address_to_sorted_storage_keys.get(&contract_address) {
            for key in storage_keys {
                alias_updater.insert_alias(key)?;
            }
        }
        alias_updater.insert_alias(&StorageKey(contract_address.0))?;
    }

    alias_updater.finalize_updates()
}
```

**File:** crates/blockifier/src/bouncer.rs (L566-577)
```rust
#[derive(Debug, PartialEq)]
#[cfg_attr(test, derive(Clone))]
pub struct Bouncer {
    // Additional info; maintained and used to calculate the residual contribution of a transaction
    // to the accumulated weights.
    pub visited_storage_entries: HashSet<StorageEntry>,
    pub state_changes_keys: StateChangesKeys,
    pub bouncer_config: BouncerConfig,
    accumulated_weights: TxWeights,
    block_full_recorded: bool,
}

```
