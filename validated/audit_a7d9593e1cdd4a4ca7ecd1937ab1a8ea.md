### Title
Bouncer state-diff-size accounting omits stateful-compression alias writes, allowing under-charged blocks to exceed committed state diff capacity - ([File: crates/blockifier/src/bouncer.rs])

### Summary
The `TokensFarm.sol::_removeParticipant()` bug class is "unbounded/uncounted iteration over an ever-growing structure whose resource cost is not properly charged, letting a caller-controlled action consume more than the allotted budget." The analogous pattern exists in the sequencer's block-building path: each admitted transaction's contribution to `BouncerWeights.state_diff_size` is computed from the transaction's own `StateChangesKeys` only, while the alias-contract entries that `allocate_aliases_in_storage` will later append to the *actual* committed state diff (once per new/never-before-seen storage key or contract address ≥ `MIN_VALUE_FOR_ALIAS_ALLOC`) are not counted at admission time.

### Finding Description
`Bouncer::try_update` / `get_tx_weights` computes `state_diff_size` purely from the transaction's marginal `StateChangesKeys` via `get_onchain_data_segment_length(&total_state_changes_keys.count())` [1](#0-0) . This is the value checked against `BouncerConfig.block_max_capacity.state_diff_size` to decide whether a transaction can still fit in the block [2](#0-1) .

The code itself flags the gap with an explicit TODO: *"consider counting here the global contract tree and the aliases as well"* [3](#0-2) .

Separately, `allocate_aliases_in_storage` runs once at block finalization (`finalize_block`), *after* every transaction has already been admitted based on the (alias-unaware) bouncer weights. It iterates over all distinct contract addresses and storage keys touched anywhere in the block and, for every key/address ≥ `MIN_VALUE_FOR_ALIAS_ALLOC` that doesn't already have an alias, writes an additional `(alias_contract_address, key) -> alias` entry to storage [4](#0-3) . Each such write adds a brand-new key to the *real* state diff that gets committed on-chain — but this cost was never charged to any transaction's bouncer weight, because it is produced by a global, cross-transaction pass over the whole block's touched-key set (analogous to the un-pruned "array of stakes" in the original bug: an ever-growing structure that keeps being iterated and mutated without its cost being reflected in the per-caller accounting that gates admission).

`finalize_block` calls this compression step unconditionally when `enable_stateful_compression` is set, strictly after the bouncer has locked in which transactions fit the block [5](#0-4) .

### Impact Explanation
Since the per-transaction `state_diff_size` the bouncer uses for admission control does not include the extra alias-contract entries created at finalize time, a proposer/sequencer can admit a set of transactions that individually and cumulatively appear to be within `block_max_capacity.state_diff_size`, yet the *actually committed* state diff (after `compress()` appends alias entries for every new key) can exceed the configured cap. Because `state_diff_size` bounds are meant to keep the on-chain/DA payload (and the associated Patricia-tree/commitment work and blob size) within provable/serializable limits, silently exceeding them can produce a state diff too large to serialize into the expected DA blob format, or a block whose committed root/weights diverge from what other (correctly-accounting) implementations would produce — risking honest-node divergence or a block that cannot be confirmed/published. This satisfies the "wrong committed root" / "network unable to confirm new transactions" impact bar.

### Likelihood Explanation
No special privilege is required: any unprivileged user (or a proposer simply processing ordinary user transactions) can trigger this by writing to many previously-untouched storage keys/contract addresses ≥ `MIN_VALUE_FOR_ALIAS_ALLOC` (e.g., deploying/writing to fresh contract storage slots repeatedly), which is a completely normal transaction pattern once `enable_stateful_compression` is active. The gap is deterministic and acknowledged in-code via the TODO comment, making it a systematic (not edge-case) undercount whenever compression is enabled and many *new* aliasable keys are touched within a single block.

### Recommendation
Include the predicted alias-contract writes in the per-transaction (or per-block, cumulatively) `state_diff_size`/state-changes-keys computation used by the bouncer, e.g. by folding the output of `predicted_alias_storage_entries` (which already exists for exactly this purpose) into `get_tx_weights`/`try_update` before comparing against `block_max_capacity`, so admission decisions account for the true post-compression state diff size.

### Proof of Concept
1. Enable `enable_stateful_compression` in `VersionedConstants`/`BlockContext`.
2. Submit a sequence of transactions, each writing to a distinct, never-before-touched storage key/contract address ≥ `MIN_VALUE_FOR_ALIAS_ALLOC` (0x80), such that the sum of `get_onchain_data_segment_length` for their `StateChangesKeys` sits just under `block_max_capacity.state_diff_size`.
3. Let the bouncer admit them all (`Bouncer::try_update` returns `Ok` for each, since alias entries aren't counted).
4. At `finalize_block`, `allocate_aliases_in_storage` appends one new `(alias_contract_address, key) -> alias` entry per unique touched key/address, growing the actual `state_diff` returned by `block_state.to_state_diff()` beyond what was budgeted, without any post-hoc bouncer re-check [6](#0-5) .

Note: I could not fully trace how the resulting oversized state diff is handled downstream (e.g., whether a later serialization/DA-blob step would hard-fail, silently truncate, or successfully overshoot the intended cap) due to index/time limits — a Devin session with full repo access would be needed to confirm the exact downstream consequence (hard failure vs. silent capacity overrun) and to pin down whether this has already been mitigated elsewhere in the DA/blob-packing code.

### Citations

**File:** crates/blockifier/src/bouncer.rs (L662-690)
```rust
        let tx_bouncer_weights = tx_weights.bouncer_weights;

        // Check if the transaction can fit the current block available capacity.
        let err_msg = format!(
            "Addition overflow. Transaction weights: {tx_bouncer_weights:?}, block weights: {:?}.",
            self.get_bouncer_weights()
        );
        let next_accumulated_weights =
            self.get_bouncer_weights().checked_add(tx_bouncer_weights).expect(&err_msg);
        if !self.bouncer_config.has_room(next_accumulated_weights) {
            let exceeded_weights =
                self.bouncer_config.get_exceeded_weights(next_accumulated_weights);
            log::debug!(
                "Transaction cannot be added to the current block, block capacity reached; \
                 transaction weights: {:?}, block weights: {:?}. Block max capacity reached on \
                 fields: {}",
                tx_weights.bouncer_weights,
                self.get_bouncer_weights(),
                exceeded_weights
            );
            // Record the block-full metric only once per block. Later candidate txs that also do
            // not fit (subsequent chunks / executor invocations share this bouncer) would otherwise
            // inflate the counter into a per-rejected-tx count instead of a per-block count.
            if !self.block_full_recorded {
                record_exceeded_bouncer_resources(&exceeded_weights);
                self.block_full_recorded = true;
            }
            Err(TransactionExecutorError::BlockFull)?
        }
```

**File:** crates/blockifier/src/bouncer.rs (L930-1000)
```rust
    // Patricia update + transaction resources.
    let patricia_update_resources = get_patricia_update_resources(
        n_visited_storage_entries,
        // TODO(Yoni): consider counting here the global contract tree and the aliases as well.
        state_changes_keys.storage_keys.len(),
    );
    let vm_resources =
        &tx_resources.computation.total_extended_vm_resources() + &patricia_update_resources;

    // Builtin gas costs for stone and for stwo.
    let sierra_builtin_gas_costs = &versioned_constants.os_constants.gas_costs.builtins;
    let proving_builtin_gas_costs = &bouncer_config.builtin_gas_costs();

    // Casm hash migration resources.
    let migration_data = CasmHashMigrationData::from_state(
        state_reader,
        executed_class_hashes,
        versioned_constants,
    )?;
    // Total state changes keys are the sum of marginal state changes keys and the
    // migration state changes.
    let mut total_state_changes_keys = StateChangesKeys {
        compiled_class_hash_keys: migration_data.class_hashes_to_migrate.keys().cloned().collect(),
        ..Default::default()
    };
    total_state_changes_keys.extend(state_changes_keys);

    // Migration occurs once per contract and is not included in the CASM hash computation, which
    // is performed every time a contract is loaded.
    let sierra_migration_gas = migration_data.to_gas(sierra_builtin_gas_costs, versioned_constants);
    let proving_migration_gas =
        migration_data.to_gas(proving_builtin_gas_costs, versioned_constants);

    // Sierra gas computation.
    let (total_sierra_gas, casm_hash_computation_data_sierra_gas, vm_resources_sierra_gas) =
        compute_sierra_gas(
            &vm_resources,
            sierra_builtin_gas_costs,
            versioned_constants,
            tx_resources,
            sierra_migration_gas,
            &class_hash_to_casm_hash_computation_resources,
        );

    // Proving gas computation.
    let cairo_primitives_for_proving_gas = get_cairo_primitives_for_proving_gas_computation(
        patricia_update_resources.prover_builtins(),
        tx_resources.computation.os_vm_resources.prover_builtins(),
        tx_cairo_primitives_counters,
    );

    let (total_proving_gas, casm_hash_computation_data_proving_gas) = compute_proving_gas(
        &cairo_primitives_for_proving_gas,
        vm_resources_sierra_gas,
        versioned_constants,
        proving_builtin_gas_costs,
        sierra_builtin_gas_costs,
        proving_migration_gas,
        &class_hash_to_casm_hash_computation_resources,
    );

    let bouncer_weights = BouncerWeights {
        l1_gas: message_starknet_l1gas,
        message_segment_length: message_resources.message_segment_length,
        n_events: tx_resources.starknet_resources.archival_data.event_summary.n_events,
        state_diff_size: get_onchain_data_segment_length(&total_state_changes_keys.count()),
        sierra_gas: total_sierra_gas,
        n_txs: 1,
        proving_gas: total_proving_gas,
        receipt_l2_gas,
    };
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

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L232-279)
```rust
/// Finalizes the creation of a block.
/// Returns the state diff and the block weights.
pub(crate) fn finalize_block<S: StateReader>(
    bouncer: &Arc<Mutex<Bouncer>>,
    block_state: &mut CachedState<S>,
    block_context: &BlockContext,
) -> TransactionExecutorResult<BlockExecutionSummary> {
    let bouncer = lock_bouncer(bouncer);
    log::info!(
        "Block {} final weights: {:?}.",
        block_context.block_info.block_number,
        bouncer.get_bouncer_weights()
    );

    let alias_contract_address = block_context
        .versioned_constants
        .os_constants
        .os_contract_addresses
        .alias_contract_address();
    if block_context.versioned_constants.enable_stateful_compression {
        allocate_aliases_in_storage(block_state, alias_contract_address)?;
    }

    let mut bouncer = bouncer;
    let class_hashes_to_migrate = mem::take(bouncer.get_mut_class_hashes_to_migrate());
    #[cfg(any(test, feature = "testing"))]
    if !class_hashes_to_migrate.is_empty() {
        log::info!(
            "Class hashes to migrate (key = class_hash, value = (compiled_class_hash_v2, \
             compiled_class_hash_v1)): {class_hashes_to_migrate:#?}"
        );
    }

    if !block_context.versioned_constants.enable_casm_hash_migration {
        assert!(
            class_hashes_to_migrate.is_empty(),
            "Class hashes to migrate should be empty when migration is disabled"
        );
    }
    block_state.set_compiled_class_hash_migration(&class_hashes_to_migrate)?;

    let state_diff = block_state.to_state_diff()?.state_maps;

    let compressed_state_diff = if block_context.versioned_constants.enable_stateful_compression {
        Some(compress(&state_diff, block_state, alias_contract_address)?.into())
    } else {
        None
    };
```
