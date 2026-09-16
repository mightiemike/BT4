### Title
Quadratic-cost alias allocation/replacement in Starknet OS state compression enables underpriced resource-exhaustion during block re-execution - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo)

### Summary
The Starknet OS's stateful-compression alias mechanism resolves each modified storage key/contract address to a compact "alias" using a linear scan (`find_element`) over the full set of aliases touched in the block. Because this lookup is performed once per distinct qualifying storage-diff entry (via `get_alias_of_big_key` / `replace_storage_diff_big_keys`), the total OS work for alias replacement is `O(n²)` in the number of distinct new/modified storage keys `n` in a block, while the fee/bouncer accounting that gates transaction/block admission charges only linearly for state-diff size. This mirrors the reported ERC-1155 `_upsertGroup` bug class: cheap, attacker-controllable growth of a per-block "array" (the squashed aliases dict) whose per-element cost is charged as O(1)/O(n) but whose real cost is O(n) per lookup, driven purely by unprivileged transaction senders writing to many distinct storage slots.

### Finding Description
`allocate_aliases_for_storage_diff_big_keys` and `replace_storage_diff_big_keys` recurse over every storage-diff entry with a key `>= MIN_VALUE_FOR_ALIAS_ALLOC`, and for each entry call `get_alias_of_big_key`, which performs a `find_element` lookup: [1](#0-0) 

`get_alias_of_big_key` invokes `find_element`, a linear search (`search_sorted_optimistic`) over the entire `aliases` array collected for the block: [2](#0-1) 

`replace_storage_diff_big_keys` calls this lookup once per storage entry, recursively, for every distinct big key in the state diff: [3](#0-2) 

Since `aliases.len` grows with the total number of distinct compressible keys touched in the block (contract addresses and storage keys above `MIN_VALUE_FOR_ALIAS_ALLOC`), and the lookup is repeated once per key, the total cost of `replace_aliases_and_serialize_full_contract_state_diff` is quadratic in the number of distinct storage/contract keys modified in the block, not linear. The equivalent allocation-side function (`allocate_aliases_for_storage_diff_big_keys`) calls `maybe_allocate_alias_for_big_key`, which similarly needs to check existing aliases, compounding the effect.

This is invoked unconditionally at the end of every block whose `enable_stateful_compression` flag is set, from `finalize_block` in the blockifier, which is fully driven by ordinary transaction execution (any unprivileged sender's contract calls that touch many distinct storage cells): [4](#0-3) 

The Rust-side "compress" and `allocate_aliases_in_storage` functions that mirror this logic in the blockifier iterate once per contract/storage key (`O(n)` in Rust with HashMap lookups) but the actual state root computation and OS re-execution (used to prove the block and by full nodes/Starknet OS re-execution to validate state transitions) uses the O(n) `find_element` linear scan per key shown above, which is the expensive path that must be Cairo-VM-stepped and proven.

Because the bouncer accounts for blocks using linear metrics — `state_diff_size` capacity (e.g. 4000) — and does not model the quadratic alias-resolution cost of the OS: [5](#0-4) 
an attacker who fills a block with many distinct new storage keys (each individually cheap and within the linear state-diff-size budget) can drive the OS's alias-replacement step toward its worst case (`O(n²)`) without being charged for the excess Cairo-step cost, since the per-transaction fee model only accounts for storage writes linearly.

### Impact Explanation
This is directly analogous to the reported vulnerability class: cheap, attacker-controlled growth of a shared array (the block's alias/storage-diff set) whose real per-element traversal cost is amortized against every other element already inserted, producing quadratic blowup from repeated legitimate user actions (writing to storage). Here the "victim" iteration point is not a per-account balance check but the mandatory OS alias-compression step that every block with `enable_stateful_compression` must run to build/verify the state diff used for L1 data availability and OS-level proof generation. If the resulting Cairo-step cost is disproportionate to what the block's declared/charged resource weights assume, it can cause:
- Excessive OS execution/proving time relative to what the sequencer/prover pipeline is budgeted for, risking missed proving deadlines and stalling of block finalization for the affected block (a network unable to confirm/finalize new blocks in a timely fashion).
- Divergence between the (cheap, linear) blockifier-side bouncer accounting and the (expensive, quadratic) OS-side re-execution cost, undermining the resource-accounting guarantee that block execution cost is properly priced and bounded.

### Likelihood Explanation
Reachable purely by unprivileged transaction senders: any account or contract that performs many distinct storage writes (e.g., writing to N different storage slots in one or a handful of transactions) can maximize the number of distinct "big keys" subject to alias allocation in a block, up to the block's configured `state_diff_size` capacity. No special privileges, staking, or operator/prover collusion are needed — an ordinary invoke transaction (or a handful of transactions from different senders) filling the state-diff budget is sufficient to trigger the worst-case quadratic alias-resolution work in every block that has stateful compression enabled.

### Recommendation
- **Short term:** Replace the `find_element` linear scan for alias lookups with a data structure/algorithm with true O(log n) (binary search over a truly sorted contiguous array, verified sorted) or O(1) amortized lookup (e.g., a squashed dict access pattern that guarantees single-pass resolution), and/or explicitly bound and charge for the quadratic cost in the bouncer/fee model (e.g., account for `n_distinct_aliased_keys²` rather than only linear state-diff size) so that the priced resource matches the real OS Cairo-step cost.
- **Long term:** Redesign alias allocation/replacement to process storage diffs in a single sorted pass (merge-style, O(n log n) or O(n)) instead of independent per-key linear lookups against a shared array, and add a hard cap (independent from `state_diff_size`) on the number of aliasable keys processed per block to prevent the compression step's cost from scaling worse than linearly with block content.

### Proof of Concept
1. Enable `enable_stateful_compression` (the default for current/recent Starknet versions per `versioned_constants`).
2. Deploy or use an existing contract with a function that writes to `N` distinct new storage slots (e.g., `storage_var_at(i)` writes for `i` in `0..N`) where `N` is chosen so the aggregate state-diff entries across the block approach `bouncer_config.block_max_capacity.state_diff_size` (default 4000, see `crates/apollo_node/resources/config_schema.json:122-126`).
3. Submit transactions from one or more unprivileged senders that collectively hit this cap for a single block.
4. Observe that `finalize_block` invokes `allocate_aliases_in_storage`/`compress` (blockifier) and, correspondingly, the Starknet OS performs `allocate_aliases`/`replace_aliases_and_serialize_full_contract_state_diff`, each of which resolves aliases via `find_element` per distinct big key (`crates/apollo_starknet_os_program/.../aliases.cairo:360-410`) — i.e., ~`N` linear scans each up to length `N`, an `O(N²)` Cairo-step cost, while the on-chain fee/bouncer model only charged for `N` linear state-diff entries.
5. Compare the Cairo VM step count/time consumed by the alias-replacement phase against the block's declared `proving_gas`/`sierra_gas` budgets to confirm the disproportion between charged and actual OS-side cost.

(Note: I could not fully verify the exact worst-case ordering/adversarial-key arrangement needed to maximize `find_element`'s scan length, nor confirm definitively that no additional capacity limiter specifically bounds the number of "big keys" independent of `state_diff_size`; a Devin session with the ability to run the Cairo OS program and benchmark step counts would be needed to empirically confirm the quadratic blowup magnitude and its real-world proving-time impact.)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo (L360-382)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo (L400-410)
```text
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

**File:** crates/starknet_os/src/hints/hint_implementation/find_element.rs (L9-37)
```rust
pub(crate) fn search_sorted_optimistic(mut ctx: HintContext<'_>) -> OsHintResult {
    let array_ptr = ctx.get_ptr(Ids::ArrayPtr)?;
    let elm_size = felt_to_usize(&ctx.get_integer(Ids::ElmSize)?)?;

    if elm_size == 0 {
        return Err(OsHintError::AssertionFailed {
            message: format!("Invalid value for elm_size. Got: {elm_size}."),
        });
    }

    let n_elms = felt_to_usize(&ctx.get_integer(Ids::NElms)?)?;

    let key = &ctx.get_integer(Ids::Key)?;

    let mut index = n_elms;
    let mut exists = false;

    // TODO(Nimrod): Verify that it's ok to ignore the `__find_element_max_size` variable.
    for i in 0..n_elms {
        let address = (array_ptr + (elm_size * i))?;
        let value = ctx.vm.get_integer(address)?;

        if value.as_ref() >= key {
            index = i;
            exists = value.as_ref() == key;

            break;
        }
    }
```

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L246-253)
```rust
    let alias_contract_address = block_context
        .versioned_constants
        .os_constants
        .os_contract_addresses
        .alias_contract_address();
    if block_context.versioned_constants.enable_stateful_compression {
        allocate_aliases_in_storage(block_state, alias_contract_address)?;
    }
```

**File:** crates/apollo_node/resources/config_schema.json (L122-126)
```json
  "batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.state_diff_size": {
    "description": "An upper bound on the total state diff size in a block.",
    "privacy": "Public",
    "value": 4000
  },
```
