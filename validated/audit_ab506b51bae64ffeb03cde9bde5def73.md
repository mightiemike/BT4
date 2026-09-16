### Title
Integer division in `calculate_next_base_gas_price`'s minimum-price ramp can permanently stall the L2 gas price below the protocol minimum - ([File: crates/apollo_consensus_orchestrator/src/fee_market/mod.rs])

### Summary
`calculate_next_base_gas_price` implements a "gradual ramp" that is supposed to raise the L2 gas price by at most `1/MIN_GAS_PRICE_INCREASE_DENOMINATOR` per block whenever the current price is below the enforced minimum. The ramp step is computed with integer division (`price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR`), which truncates to `0` whenever `price.0 < MIN_GAS_PRICE_INCREASE_DENOMINATOR` (333). Once the price falls into that range, the "increase" added every block is `0`, so the price can never climb back toward `min_gas_price` — it is permanently stuck, exactly the same truncation-to-threshold failure pattern as the reported `UptimeTracker::computeValidatorUptime` bug (a per-step integer-division remainder that is silently and irrecoverably dropped, defeating the intended convergence/threshold logic).

### Finding Description
The vulnerable logic is: [1](#0-0) 

```rust
if price < min_gas_price {
    let max_increase = price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR; // integer division
    let adjusted = price.0 + max_increase;
    let adjusted_price = adjusted.min(min_gas_price.0);
    ...
    return GasPrice(adjusted_price);
}
```

`MIN_GAS_PRICE_INCREASE_DENOMINATOR` is `333` [2](#0-1) . As with the Solidity `uptimeToDistribute / elapsedEpochs` truncation, `price.0 / 333` rounds toward zero. If `price.0` is anywhere in `[0, 332]`, `max_increase` is `0`, so `adjusted == price`, and the function returns the *same* price it was called with. Because the very same branch condition (`price < min_gas_price`) is re-evaluated identically on every subsequent block with the same input, the price never leaves the stalled state on its own — the truncated remainder that should eventually accumulate into a positive increase is dropped every single block, forever, once the price is small enough. This mirrors the audit's core observation that "the lost remainder is never recovered in future calculations."

This function is called every block to compute the L2 gas price used for the next block via `calculate_next_l2_gas_price_for_fin` [3](#0-2) , which every proposer and every validating node must derive identically since it feeds directly into the block's committed fee-market info and gas pricing used to charge L2 gas fees on all transactions.

### Impact Explanation
Once the price drops into `[0, 332]` fri (e.g., following a sustained period of low `l2_gas_used` relative to `gas_target`, which lowers price toward the floor over many blocks per the normal EIP-1559-style decrease branch below), the minimum-price ramp becomes permanently inert. From that point on, transactions are priced at (or effectively pinned near) an L2 gas price far below the protocol-intended `min_gas_price`, indefinitely — this is a fee/resource-accounting correctness defect: the sequencer systematically undercharges L2 gas resource usage relative to protocol policy, forever, with no self-correction. This under-pricing can be leveraged by any ordinary transaction sender: heavy L2-gas consumption becomes persistently cheap, enabling economically viable resource-exhaustion/spam against block building and execution capacity (bouncer / gas accounting), since the fee mechanism designed to throttle demand back toward `gas_target` can never restore its floor.

### Likelihood Explanation
No malicious or privileged action is required — an unprivileged sequence of ordinary transactions (or lack thereof) driving `l2_gas_used` below `gas_target` for a sustained period is sufficient to walk the price down through the decrease branch until it enters `[0, 332]`. From that point, the price is stuck by simple arithmetic (`x / 333 == 0` for `x < 333`), deterministically and permanently, independent of any further attacker action — this is not a rare edge case requiring adversarial crafting, it is a natural consequence of the EIP-1559-style decrease mechanism eventually reaching a small value.

### Recommendation
Avoid truncating division for the ramp step. Options mirroring the original report's recommendations:
- Guarantee a minimum non-zero step (e.g., `max(1, price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR)`) so the price always makes forward progress toward `min_gas_price` when below it.
- Alternatively, use `div_ceil` (round up) for the increase computation instead of floor division, ensuring the remainder is never dropped and the price provably converges to `min_gas_price` in a bounded number of blocks.

### Proof of Concept
```rust
// crates/apollo_consensus_orchestrator/src/fee_market/mod.rs
// Demonstration of the stall condition.
let price = GasPrice(100);            // < MIN_GAS_PRICE_INCREASE_DENOMINATOR (333)
let min_gas_price = GasPrice(10_000); // price is far below minimum
let gas_used = GasAmount(0);
let gas_target = GasAmount(1_000_000);

let next = calculate_next_base_gas_price(price, gas_used, gas_target, min_gas_price);
assert_eq!(next, price); // max_increase = 100 / 333 = 0 -> no progress

// Calling again with the returned price reproduces the exact same result forever:
let next2 = calculate_next_base_gas_price(next, gas_used, gas_target, min_gas_price);
assert_eq!(next2, price); // permanently stalled below min_gas_price
```

### Citations

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L17-23)
```rust
// Denominator for the maximum gas price increase per block when price is below minimum.
// This controls how quickly the gas price can rise towards the minimum.
//
// With a denominator of 333: Each block can increase by at most 0.3% of the current price, to
// double the price takes approximately 230 blocks (at 2.6 seconds per block), this means doubling
// in approximately 10 minutes.
const MIN_GAS_PRICE_INCREASE_DENOMINATOR: u128 = 333;
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L96-97)
```rust
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
