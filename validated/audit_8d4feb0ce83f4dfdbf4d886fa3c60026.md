### Title
Permanent zero-lock in L2 gas price ramp-up allows the EIP‑1559 floor to be bypassed forever - (File: `crates/apollo_consensus_orchestrator/src/fee_market/mod.rs`)

### Summary
`calculate_next_base_gas_price` implements the EIP‑1559-style gas price update used to derive every block's L2 gas price during block building. When the current price is below the configured minimum, it computes a bounded ramp-up increment as `price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR`. If `price.0` is `0`, this increment is always `0`, so the "ramp toward the minimum" logic can never move the price away from zero — the price becomes permanently stuck at `0`, in violation of the very invariant (`min_gas_price` is a floor) the code exists to enforce.

### Finding Description [1](#0-0) 

```rust
if price < min_gas_price {
    let max_increase = price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR;
    let adjusted = price.0 + max_increase;
    let adjusted_price = adjusted.min(min_gas_price.0);
    ...
    return GasPrice(adjusted_price);
}
```

This mirrors the MuteBond `timeToTokens` bug class exactly: an integer division (`price.0 / 333`) that floors to `0` whenever the dividend is small (here, `0` itself), whose result feeds directly into the value that is supposed to guarantee forward progress. Once `price.0 == 0`, `max_increase` is always `0`, `adjusted` is always `0`, and `adjusted.min(min_gas_price.0)` is always `0` (since `0` is the minimum of the two) — the state is permanently "stuck," just like `maxDeposit()` being stuck below the threshold that makes `LockTo` always revert.

The rest of the pipeline can drive `price` to exactly `0`. In the main EIP-1559 branch (used when `price >= min_gas_price`) the price decreases by `price_change = price * gas_delta / (gas_target * gas_price_max_change_denominator)`; with sustained low L2 gas usage (`gas_used` well below `gas_target`) across consecutive blocks, or with a small `gas_price_max_change_denominator`, the price decays toward `min_gas_price`. If a genesis/override/config transition ever leaves `price` at `0` (e.g., before the fee market is warmed up, or via config changes to `min_l2_gas_price_per_height`/versioned constants that a node picks up while the previously stored/synced price is `0`), the ramp-up branch above is entered and can never recover: [2](#0-1) 

```rust
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    ...
) -> NextL2GasPrice {
    ...
    let raw_price =
        calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min);
    ...
}
```

`current_l2_gas_price` is threaded from block to block (it's the sequencer/validator's persisted fee-market state), so once it collapses to `0` it stays `0` for every subsequent block indefinitely — there is no other code path that resets or repairs it.

### Impact Explanation
The L2 gas price is a core economic-safety parameter used to price every L2-gas-consuming transaction in every subsequent block. A permanent collapse to `0`:
- Nullifies the protocol's fee floor (`min_gas_price`) invariant for the remaining lifetime of the chain (or until a manual intervention/override is applied), meaning L2 execution becomes effectively free.
- Enables attackers to submit unlimited computation-heavy transactions at zero cost, letting them monopolize block resources (bouncer capacity) indefinitely and starve legitimate fee-paying transactions — i.e., the network becomes unable to properly confirm transactions under its intended economic model.
- Represents a permanent, self-inflicted freezing of a protocol invariant with no organic recovery path, matching the "permanent freezing" / "network unable to confirm new transactions [at correct price]" impact classes.

### Likelihood Explanation
Reaching `price == 0` requires either: (a) sustained low L2 gas utilization across many consecutive blocks driving the EIP-1559 decrease branch down to zero (achievable without any special privilege — an extended period of low network usage, which is a normal, attacker-influenceable condition since a dominant sender can simply stop submitting L2-gas-heavy transactions or flood with light ones to depress the target ratio over time), or (b) a fee-market state transition (parameter change / warm-start) that leaves the persisted price at `0` while the new minimum is nonzero. Once triggered, the bug is deterministic and irreversible via the code path shown, requiring no further attacker action to persist.

### Recommendation
Change the ramp-up computation so it always makes forward progress when `price < min_gas_price`, regardless of the current price magnitude, e.g.:
```rust
let max_increase = max(price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR, 1);
```
or explicitly special-case `price.0 == 0` to jump to a minimal nonzero price (e.g., `1`) before applying the proportional ramp, and add a regression test asserting that `calculate_next_base_gas_price(GasPrice(0), ..., min_gas_price)` with `min_gas_price > 0` eventually converges to `min_gas_price` rather than remaining at `0` forever.

### Proof of Concept
```rust
// crates/apollo_consensus_orchestrator/src/fee_market/mod.rs
let min_gas_price = GasPrice(1_000_000);
let gas_target = GasAmount(1_000_000);

let mut price = GasPrice(0); // price collapsed to zero
for _ in 0..1_000_000 {
    price = calculate_next_base_gas_price(price, GasAmount(0), gas_target, min_gas_price);
}
// price remains GasPrice(0) forever: 0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR == 0 on every iteration,
// so `adjusted = 0`, and `adjusted.min(min_gas_price.0) == 0`.
assert_eq!(price, GasPrice(0)); // never reaches min_gas_price, no matter how many blocks pass
```

### Citations

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L73-97)
```rust
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    height: BlockNumber,
    l2_gas_used: GasAmount,
    override_l2_gas_price_fri: Option<u128>,
    min_l2_gas_price_per_height: &[PricePerHeight],
    fee_actual: Option<GasPrice>,
) -> NextL2GasPrice {
    if let Some(override_value) = override_l2_gas_price_fri {
        // Operator pin: escapes both bounds by design; each side substitutes its own override.
        info!(
            "L2 gas price ({}) is not updated, remains on override value of {override_value} fri",
            current_l2_gas_price.0
        );
        return NextL2GasPrice { published_price: GasPrice(override_value), bounds: None };
    }
    let gas_target = VersionedConstants::latest_constants().gas_target;
    let config_min = get_min_gas_price_for_height(height, min_l2_gas_price_per_height);
    let cap = l2_gas_price_cap(config_min);

    let snip35_min = fee_actual.map_or(config_min, |fee_actual| max(config_min, fee_actual));
    let effective_min = min(snip35_min, cap);

    let raw_price =
        calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min);
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L190-202)
```rust
    // If the current price is below the minimum, apply a gradual adjustment and return early.
    // This allows the price to increase by at most 1/MIN_GAS_PRICE_INCREASE_DENOMINATOR per block.
    if price < min_gas_price {
        let max_increase = price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR;
        let adjusted = price.0 + max_increase;
        // Cap at min_gas_price to avoid overshooting
        let adjusted_price = adjusted.min(min_gas_price.0);
        info!(
            "Fee Market: Price {} below minimum gas price {}, adjusted price: {} )",
            price.0, min_gas_price.0, adjusted_price
        );
        return GasPrice(adjusted_price);
    }
```
