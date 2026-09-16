## Analysis

The reported bug class is a **fixed-point/integer-division truncation to zero**: dividing a small numerator by a large denominator in unsigned integer arithmetic silently yields `0`, breaking downstream accounting logic that assumes a non-zero result.

I found a structurally identical pattern in the sequencer's bouncer weight accounting, which governs per-block resource capacity enforcement — one of the explicitly in-scope domains. [1](#0-0) 

### Title
Integer-division truncation to zero in `BuiltinInstanceLimits::induced_gas_costs` can zero out a builtin's proving-gas weight - (File: `crates/blockifier/src/bouncer.rs`)

### Summary
`BuiltinInstanceLimits::induced_gas_costs` derives the per-instance proving-gas cost of each Cairo builtin/opcode as `floor(proving_gas / limit)`. When the configured per-builtin instance limit for any primitive is greater than or equal to the block's total `proving_gas` capacity, this floor division truncates to `0`, exactly the same failure mode as `amountToSellUnits = 0` in the referenced report (small numerator, large denominator → zero).

### Finding Description
`induced_gas_costs` is computed as:
```rust
let derive = |limit: NonZeroU64| -> u64 { proving_gas.0 / limit.get() };
``` [2](#0-1) 

The resulting `BuiltinGasCosts` (`proving_builtin_gas_costs`) is then used, via `cairo_primitives_to_gas`, to convert a transaction's actual Cairo-primitive usage counts into a proving-gas amount that is accumulated into the bouncer's `proving_gas` weight: [3](#0-2) 

That weight is what `Bouncer::try_update`/`verify_tx_weights_within_max_capacity` compares against `block_max_capacity.proving_gas` to decide whether a transaction still fits in the block: [4](#0-3) 

If, for any Cairo primitive, `induced_gas_costs` returns `0` (which happens whenever that primitive's `builtin_instance_limits` value is ≥ the configured `proving_gas` budget for the block), then every transaction using that primitive is charged **zero** proving-gas for it, no matter how many instances it uses. The bouncer will therefore never register `BlockFull` on the `proving_gas` dimension for usage of that specific primitive, and a chain of transactions can pack an unbounded amount of that builtin's real proving work into a single block while still passing the capacity check.

### Impact Explanation
`proving_gas` is the metric intended to bound the real, per-builtin proving cost of a block so that the block remains provable within the network's SHARP/proving capacity. If the divisor (a primitive's instance limit) is not kept strictly smaller than the proving-gas budget, the accounting for that primitive silently collapses to zero, allowing a block to be built that exceeds the true provable capacity for that resource. This is exactly analogous to the "impossible to rebalance" root cause: an accounting quantity that should scale with usage is annihilated by integer-division truncation, defeating the very check it was designed to enforce. Downstream, this can lead to a block that cannot actually be proven, i.e., "a network unable to confirm new transactions" — one of the explicitly accepted impact categories.

### Likelihood Explanation
Reachability only requires that the `proving_gas` block budget be misconfigured or reduced relative to a given `builtin_instance_limits` entry (e.g., via `BouncerConfig` construction paths such as `BouncerConfig::empty()`, which sets `block_max_capacity` — and hence `proving_gas` — to zero, guaranteeing every `derive(limit)` call floors to `0`) — visible at [5](#0-4) . Under the current shipped default constants the derived costs happen to remain non-zero (as shown by the regression snapshot), so the issue only manifests when `proving_gas` capacity is configured lower than an instance limit — but the *code path* itself has no minimum-value guard preventing this from silently degrading to an unenforced, zero-cost primitive whenever that configuration condition is met, unlike ceiling-rounded conversions elsewhere in the codebase (e.g., `sierra_gas_to_l1_gas_amount_round_up`, `calculate_resource_gas_cost`) which intentionally round up specifically to avoid under-counting.

### Recommendation
Change `induced_gas_costs` to round up (`ceil`) rather than floor when deriving per-instance proving-gas cost, and/or add an explicit invariant check (e.g., a debug assertion or config validation) that rejects configurations where any `builtin_instance_limits` value is ≥ `proving_gas`, ensuring the derived per-instance cost can never be zero for a resource that is actually used.

### Proof of Concept
1. Configure (or reach, via the existing `BouncerConfig::empty()`/reduced-capacity construction path) a `BouncerConfig` where `block_max_capacity.proving_gas` is `0` or smaller than `builtin_instance_limits.<some_builtin>`.
2. `BuiltinInstanceLimits::builtin_gas_costs()` → `induced_gas_costs(proving_gas)` computes `proving_gas.0 / limit.get()` for that builtin, yielding `0`.
3. Submit any transaction that heavily uses that builtin (e.g., many `range_check` or `keccak` invocations). `cairo_primitives_to_gas` multiplies the usage count by the (zero) per-unit cost, contributing `0` to `TxWeights::bouncer_weights.proving_gas`.
4. `Bouncer::try_update` accumulates zero proving-gas weight for this builtin regardless of actual usage volume, so `bouncer_config.has_room(...)` never rejects the transaction on this dimension, allowing unbounded accumulation of that builtin's real work in a single block.

### Citations

**File:** crates/blockifier/src/bouncer.rs (L91-97)
```rust
impl BouncerConfig {
    pub fn empty() -> Self {
        Self {
            block_max_capacity: BouncerWeights::empty(),
            builtin_instance_limits: BuiltinInstanceLimits::default(),
        }
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

**File:** crates/blockifier/src/bouncer.rs (L660-682)
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
            record_exceeded_bouncer_resources(&exceeded_weights);
            Err(TransactionExecutorError::BlockFull)?
        }
```

**File:** crates/blockifier/src/bouncer.rs (L804-827)
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
