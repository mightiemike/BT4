### Title
Integer-division truncation in `BuiltinInstanceLimits::induced_gas_costs` can zero out a builtin's proving-gas weight, letting a transaction bypass the bouncer's proving-gas resource accounting - ([File: crates/blockifier/src/bouncer.rs])

### Summary
`BuiltinInstanceLimits::induced_gas_costs` derives the per-instance proving-gas cost of each Cairo primitive with a plain integer division: `cost = floor(proving_gas / limit)` [1](#0-0) . Exactly like the reported USSD bug (`amountToBuyLeftUSD * 1e18 / collateralval) / 1e18` truncating to `0`), whenever the configured block-wide `proving_gas` budget is smaller than a builtin's `NonZeroU64` instance limit, this division floors to `0`, so that builtin's induced per-instance proving-gas cost becomes `0`.

### Finding Description
`compute_proving_gas`/`get_tx_weights` use `proving_builtin_gas_costs` (derived via `induced_gas_costs`) to convert a transaction's Cairo-primitive usage counters into a `proving_gas` contribution that is added to the bouncer's accumulated `BouncerWeights.proving_gas` [2](#0-1) . The conversion for each primitive is `cairo_primitive_weight.checked_mul(count)` where `cairo_primitive_weight` comes from `BuiltinGasCosts::get_cairo_primitive_gas_cost`, itself populated from `induced_gas_costs` [3](#0-2) .

If the operator's configured `block_max_capacity.proving_gas` is lower than a given builtin's `BuiltinInstanceLimits` value (e.g. `keccak: nz(10_000)`, `range_check: nz(66_666_666)`), `derive(limit)` computes `proving_gas.0 / limit.get()`, which truncates to `0` for that builtin [4](#0-3) . Any transaction reachable by an ordinary account (an unprivileged tx sender simply invokes a contract that heavily uses that builtin, e.g. many `keccak` operations) will then contribute `count * 0 = 0` proving-gas to `BouncerWeights.proving_gas`, regardless of how many instances of that builtin it actually consumes.

### Impact Explanation
Because the bouncer's `within_max_capacity_or_err`/`has_room` check is the sole per-block admission gate protecting the prover's real workload budget [5](#0-4) , a builtin whose induced cost floors to zero effectively has unlimited "free" capacity in the bouncer's accounting even though the actual proving cost of many instances of that builtin is non-zero. This allows the sequencer to admit and build blocks whose real proving workload (for that specific builtin) exceeds what the prover can process, i.e., the bouncer weight accounting silently diverges from the true resource consumption. This can produce blocks that cannot be proven within the intended resource/cost bounds, undermining the resource-accounting invariant the bouncer is meant to enforce and potentially leading to a chain that cannot progress (unprovable/overloaded blocks) — a network-availability impact.

### Likelihood Explanation
Whether this is *reachable* depends entirely on operator-chosen configuration values (`BouncerConfig.block_max_capacity.proving_gas` vs `BuiltinInstanceLimits`), not on anything a transaction sender controls directly. The default limits shown (`BuiltinInstanceLimits::default()`) are large (millions), so under default production constants this specific truncation likely does not trigger with default `proving_gas` capacity — I could not fully confirm the default `BouncerWeights::proving_gas` capacity value within the available search results, so I cannot definitively state whether current default configuration triggers the zero case. This uncertainty is significant: the vulnerability is a genuine latent arithmetic bug (unguarded floor-division that can silently zero a resource cost), but its exploitability depends on config values I was not able to fully verify from the indexed code, and it is arguably a bouncer/configuration-tuning bug rather than a bug directly triggerable via a specific malicious transaction shape (any transaction can be crafted to exercise the affected builtin, but the actual "cost = 0" state requires an operator misconfiguration, which is outside strict unprivileged-attacker control).

### Recommendation
Guard `induced_gas_costs` against floor-to-zero results, e.g. by clamping the derived cost to a minimum of `1` (or explicitly returning an error/panic at config-validation time if `limit > proving_gas`, since a limit larger than the total budget is nonsensical), mirroring how the reported analog fixes the truncation by removing the erroneous extra division. Concretely, change:
```rust
let derive = |limit: NonZeroU64| -> u64 { proving_gas.0 / limit.get() };
```
to a saturating/ceiling scheme that guarantees `derive(limit) >= 1` whenever `limit <= proving_gas` is a valid configuration, and validate at config-load time that no builtin's instance limit exceeds `block_max_capacity.proving_gas` (or explicitly document/allow it while ensuring the zero-cost primitives cannot be used to bypass the proving_gas cap — e.g., by capping instance counts per block for such primitives independently).

### Proof of Concept
Given the code:
```rust
// crates/blockifier/src/bouncer.rs
pub fn induced_gas_costs(&self, proving_gas: GasAmount) -> BuiltinGasCosts {
    let derive = |limit: NonZeroU64| -> u64 { proving_gas.0 / limit.get() };
    BuiltinGasCosts { ..., keccak: derive(self.keccak), ... }
}
```
If an operator configures `block_max_capacity.proving_gas = GasAmount(5_000)` while `builtin_instance_limits.keccak = NonZeroU64::new(10_000)` (below default, but a valid non-zero config), then:
```
derive(keccak_limit) = 5_000 / 10_000 = 0
```
Any account transaction invoking a contract that performs, say, 50 keccak builtin operations will have its Cairo-primitive counter map record `keccak: 50`; `cairo_primitives_to_gas` computes `50 * 0 = 0` [6](#0-5) , contributing zero to `BouncerWeights.proving_gas` no matter how many keccak instances are used, while the bouncer's capacity check `has_room`/`within_max_capacity_or_err` never blocks admission on this axis [5](#0-4) .

### Citations

**File:** crates/blockifier/src/bouncer.rs (L117-137)
```rust
    pub fn has_room(&self, weights: BouncerWeights) -> bool {
        self.block_max_capacity.has_room(weights)
    }

    pub fn get_exceeded_weights(&self, weights: BouncerWeights) -> String {
        self.block_max_capacity.get_exceeded_weights(weights)
    }

    pub fn within_max_capacity_or_err(
        &self,
        weights: BouncerWeights,
    ) -> TransactionExecutionResult<()> {
        if self.block_max_capacity.has_room(weights) {
            Ok(())
        } else {
            Err(TransactionExecutionError::TransactionTooLarge {
                max_capacity: Box::new(self.block_max_capacity),
                tx_size: Box::new(weights),
            })
        }
    }
```

**File:** crates/blockifier/src/bouncer.rs (L453-471)
```rust
impl BuiltinInstanceLimits {
    /// Induces the per-instance proving-gas cost of each Cairo primitive from its per-block
    /// instance limit: `cost = floor(proving_gas / limit)`.
    pub fn induced_gas_costs(&self, proving_gas: GasAmount) -> BuiltinGasCosts {
        let derive = |limit: NonZeroU64| -> u64 { proving_gas.0 / limit.get() };
        BuiltinGasCosts {
            pedersen: derive(self.pedersen),
            range_check: derive(self.range_check),
            range_check96: derive(self.range_check96),
            poseidon: derive(self.poseidon),
            ecdsa: derive(self.ecdsa),
            ecop: derive(self.ecop),
            bitwise: derive(self.bitwise),
            keccak: derive(self.keccak),
            add_mod: derive(self.add_mod),
            mul_mod: derive(self.mul_mod),
            blake: derive(self.blake),
        }
    }
```

**File:** crates/blockifier/src/bouncer.rs (L812-835)
```rust
pub fn cairo_primitives_to_gas(
    cairo_primitives_counters: &CairoPrimitiveCounterMap,
    // NOTE: 'blake' is currently the only supported opcode, by being included in the
    // builtin_gas_costs.
    cairo_primitives_gas_costs: &BuiltinGasCosts,
) -> GasAmount {
    let cairo_primitives_gas =
        cairo_primitives_counters.iter().fold(0u64, |accumulated_gas, (name, &count)| {
            let cairo_primitive_weight =
                cairo_primitives_gas_costs.get_cairo_primitive_gas_cost(name).unwrap();
            cairo_primitive_weight
                .checked_mul(u64_from_usize(count))
                .and_then(|builtin_gas| accumulated_gas.checked_add(builtin_gas))
                .unwrap_or_else(|| {
                    panic!(
                        "Overflow while converting cairo primitives counters to gas.\nCairo \
                         primitive: {name:?}, Weight: {cairo_primitive_weight}, Count: {count}, \
                         Accumulated gas: {accumulated_gas}"
                    )
                })
        });

    GasAmount(cairo_primitives_gas)
}
```

**File:** crates/blockifier/src/bouncer.rs (L883-1000)
```rust
fn compute_proving_gas(
    cairo_primitives_counters: &CairoPrimitiveCounterMap,
    vm_resources_sierra_gas: GasAmount,
    versioned_constants: &VersionedConstants,
    proving_builtin_gas_costs: &BuiltinGasCosts,
    sierra_builtin_gas_costs: &BuiltinGasCosts,
    migration_gas: GasAmount,
    class_hash_to_casm_hash_computation_resources: &HashMap<ClassHash, ExtendedExecutionResources>,
) -> (GasAmount, CasmHashComputationData) {
    let vm_resources_proving_gas = proving_gas_from_cairo_primitives_and_sierra_gas(
        vm_resources_sierra_gas,
        cairo_primitives_counters,
        proving_builtin_gas_costs,
        sierra_builtin_gas_costs,
    );

    let proving_gas_without_casm_hash_computation =
        vm_resources_proving_gas.checked_add_panic_on_overflow(migration_gas);

    add_casm_hash_computation_gas_cost(
        class_hash_to_casm_hash_computation_resources,
        proving_gas_without_casm_hash_computation,
        proving_builtin_gas_costs,
        versioned_constants,
    )
}

#[allow(clippy::too_many_arguments)]
pub fn get_tx_weights<S: StateReader>(
    state_reader: &S,
    executed_class_hashes: &HashSet<ClassHash>,
    n_visited_storage_entries: usize,
    tx_resources: &TransactionResources,
    state_changes_keys: &StateChangesKeys,
    versioned_constants: &VersionedConstants,
    tx_cairo_primitives_counters: &CairoPrimitiveCounterMap,
    bouncer_config: &BouncerConfig,
    receipt_l2_gas: GasAmount,
) -> TransactionExecutionResult<TxWeights> {
    let message_resources = &tx_resources.starknet_resources.messages;
    let message_starknet_l1gas = usize_from_u64(message_resources.get_starknet_gas_cost().l1_gas.0)
        .expect("This conversion should not fail as the value is a converted usize.");

    // Casm hash resources.
    let class_hash_to_casm_hash_computation_resources =
        map_class_hash_to_casm_hash_computation_resources(state_reader, executed_class_hashes)?;

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
