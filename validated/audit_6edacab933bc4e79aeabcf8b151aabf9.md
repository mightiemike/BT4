### Title
Floor-division in `BuiltinInstanceLimits::induced_gas_costs` can zero out a builtin's proving-gas cost - (File: crates/blockifier/src/bouncer.rs)

### Summary
`BuiltinInstanceLimits::induced_gas_costs` derives the per-instance proving-gas cost of each Cairo primitive as `cost = proving_gas / limit`, using plain integer (floor) division, analogous to the reported `schedule = 100 / founderPct` bug where floor division degenerates for certain input ranges and defeats the intended accounting/spacing logic.

### Finding Description
`induced_gas_costs` computes, for every builtin, `proving_gas.0 / limit.get()` with truncating integer division: [1](#0-0) 

`proving_gas` here is the block-wide proving-gas budget (`BouncerConfig::block_max_capacity.proving_gas`), and `limit` is the configured per-block instance limit for that builtin (`BuiltinInstanceLimits`, e.g. `range_check: nz(66_666_666)`), used via `BouncerConfig::builtin_gas_costs`: [2](#0-1) 

Whenever `proving_gas < limit` for a given builtin, the floor division truncates to `0`, exactly mirroring the reported bug's degenerate case (`100 / founderPct` collapsing to `1` when `founderPct > 50`): here the derived per-instance proving-gas cost collapses to `0` when the configured instance limit exceeds the configured proving-gas budget for any given builtin. The resulting `BuiltinGasCosts` are then used to convert executed builtin/opcode counts into proving gas via `cairo_primitives_to_gas`, which is summed into `total_proving_gas` and folded into `BouncerWeights::proving_gas`, the value the bouncer checks against block capacity in `try_update`: [3](#0-2) [4](#0-3) 

If a builtin's per-instance induced cost is `0`, transactions that use that builtin contribute nothing to `total_proving_gas`, so the bouncer's capacity check (`has_room`) never accounts for it, and an unbounded number of instances of that builtin can be packed into a block without ever tripping the `BlockFull` guard on the proving-gas dimension.

### Impact Explanation
Because `receipt_l2_gas`/`sierra_gas` still track other cost dimensions but `proving_gas` is the dimension meant to bound the size of the proof the block generates for that specific Cairo primitive, a zeroed-out per-primitive cost lets a single attacker (any unprivileged transaction sender) craft transactions that repeatedly invoke the affected builtin to build a block whose real proving workload for that primitive is unbounded while the bouncer believes it is within `block_max_capacity`. This can produce a block that is accepted by the batcher/bouncer but cannot actually be proven within the intended proving-gas budget, i.e., a resource/DoS condition at the network-liveness level (an honest sequencer builds a block it cannot get proven, stalling the ability to confirm new transactions) rather than a mere fee-metering imprecision.

### Likelihood Explanation
This requires no privileged access — it is reachable purely by choosing calldata/entry points that make heavy use of one particular builtin whose configured `BuiltinInstanceLimits` value happens to exceed the configured `block_max_capacity.proving_gas`. Whether it is currently exploitable depends entirely on the deployed configuration values (the default `BuiltinInstanceLimits` in the repo are large, e.g. `range_check: nz(66_666_666)` [5](#0-4) ) versus the deployed `block_max_capacity.proving_gas`; I could not fully verify the concrete deployed default for `proving_gas` within the available context, so likelihood is config-dependent rather than universally guaranteed — flagged as an area needing confirmation with the actual production `VersionedConstants`/`BouncerConfig` defaults.

### Recommendation
Use ceiling division (or otherwise guarantee a minimum cost of `1`) when deriving `induced_gas_costs`, e.g. `proving_gas.0.div_ceil(limit.get()).max(1)`, and/or add an explicit validation at config-load time asserting `proving_gas >= limit` for every builtin (or otherwise that the derived per-instance cost is non-zero), so a misconfiguration cannot silently exempt a builtin from the proving-gas bound, mirroring the suggested `(100 / founderPct) + 1` fix in the referenced report.

### Proof of Concept
1. Configure (or observe an existing deployment where) `BouncerConfig.block_max_capacity.proving_gas < BuiltinInstanceLimits.<builtin>` for some builtin (e.g., `range_check`).
2. Call `BuiltinInstanceLimits::induced_gas_costs(proving_gas)` — the corresponding field will be `0` per [6](#0-5) .
3. Submit successive transactions that repeatedly execute that builtin (e.g., many `range_check` operations) via a normal `INVOKE` transaction.
4. In `get_tx_weights`, `compute_proving_gas`/`cairo_primitives_to_gas` will multiply the builtin usage count by the zeroed cost, contributing `0` to `total_proving_gas` regardless of how many instances are used [7](#0-6) .
5. `Bouncer::try_update` will keep accepting these transactions on the proving-gas dimension indefinitely since `next_accumulated_weights.proving_gas` never grows from this builtin's usage [4](#0-3) , producing a block whose true proving cost for that builtin is unbounded despite passing the bouncer's capacity check.

### Citations

**File:** crates/blockifier/src/bouncer.rs (L111-115)
```rust
    /// Per-Cairo-primitive proving-gas costs derived from the configured instance limits and
    /// the block-wide proving-gas budget.
    pub fn builtin_gas_costs(&self) -> BuiltinGasCosts {
        self.builtin_instance_limits.induced_gas_costs(self.block_max_capacity.proving_gas)
    }
```

**File:** crates/blockifier/src/bouncer.rs (L453-472)
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
}
```

**File:** crates/blockifier/src/bouncer.rs (L474-491)
```rust
impl Default for BuiltinInstanceLimits {
    fn default() -> Self {
        let nz = |n: u64| NonZeroU64::new(n).expect("BuiltinInstanceLimits default must be > 0");
        Self {
            pedersen: nz(2_000_000),
            range_check: nz(66_666_666),
            range_check96: nz(33_519_553),
            poseidon: nz(600_000),
            ecdsa: nz(3_000),
            ecop: nz(130_000),
            bitwise: nz(10_500_000),
            keccak: nz(10_000),
            add_mod: nz(3_000_000),
            mul_mod: nz(3_000_000),
            blake: nz(1_800_000),
        }
    }
}
```

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

**File:** crates/blockifier/src/bouncer.rs (L974-1000)
```rust
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
