### Title
`finalize_decision` computes `next_l2_gas_price` with a one-block-stale `fee_actual` floor, breaking the SNIP-35 EIP-1559 minimum - (`File: crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs`)

---

### Summary

In `finalize_decision`, `update_l2_gas_price(height, l2_gas_used)` is called on line 517 **before** `record_fee_proposal(height, init.fee_proposal_fri)` on line 518. Because `update_l2_gas_price` internally calls `compute_fee_actual(&self.fee_proposals_window, height, window_size)`, and `height`'s own `fee_proposal_fri` has not yet been inserted into the window at that point, the `fee_actual` floor used to compute `next_l2_gas_price` is always one block stale — it reads `[height − window_size, height − 1]` instead of the correct `[height − window_size + 1, height]`.

---

### Finding Description

`compute_fee_actual(height)` is defined to return the median of fee proposals for heights `[height − window_size, height − 1]` (exclusive of `height`):

```rust
// crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs, lines 61-80
let Some(start) = height.0.checked_sub(window_size) else { ... };
for source_height in (start..height.0).map(BlockNumber) {   // [start, height)
    match fee_proposals_window.get(&source_height) { ... }
}
``` [1](#0-0) 

In `finalize_decision`, the two state-mutation calls appear in this order:

```rust
// line 517 – computes fee_actual BEFORE height's proposal is in the window
self.update_l2_gas_price(height, l2_gas_used);
// line 518 – inserts height's fee_proposal into the window
self.record_fee_proposal(height, init.fee_proposal_fri);
``` [2](#0-1) 

`update_l2_gas_price` delegates to `calculate_next_l2_gas_price`, which calls `compute_fee_actual(height)`:

```rust
fn calculate_next_l2_gas_price(&self, height: BlockNumber, l2_gas_used: GasAmount) -> GasPrice {
    let fee_actual = compute_fee_actual(
        &self.fee_proposals_window,
        height,                          // ← reads [height-W, height-1], not [height-W+1, height]
        VersionedConstants::latest_constants().fee_proposal_window_size,
    );
    calculate_next_l2_gas_price_for_fin(
        self.l2_gas_price, height, l2_gas_used, ..., fee_actual,
    )
}
``` [3](#0-2) 

`fee_actual` is then used as the effective minimum floor in `calculate_next_l2_gas_price_for_fin`:

```rust
let effective_min = match fee_actual {
    Some(fa) => GasPrice(max(config_min.0, fa.0)),
    None => config_min,
};
calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
``` [4](#0-3) 

The correct floor for the gas price of block `height + 1` is `fee_actual(height + 1)` = median of `[height − window_size + 1, height]`, which **includes** the just-committed block's `fee_proposal_fri`. Because `record_fee_proposal` is called after `update_l2_gas_price`, the window is missing this entry and the floor is computed from the wrong set of blocks — a direct off-by-one analog to H-02.

The same stale `fee_actual` is also captured in `build_proposal` (line 782–786) and forwarded into `ProposalBuildArguments.fee_actual`, so the `next_l2_gas_price` embedded in the `ProposalFin` message is also wrong:

```rust
let fee_actual = compute_fee_actual(
    &self.fee_proposals_window,
    build_param.height,          // ← same off-by-one
    VersionedConstants::latest_constants().fee_proposal_window_size,
);
``` [5](#0-4) 

The resulting `next_l2_gas_price` is then written into the `BlockHeaderWithoutHash` sent to state sync (line 405) and into `FeeMarketInfo` in the cende blob (line 605):

```rust
next_l2_gas_price: self.l2_gas_price,   // already updated with wrong floor
``` [6](#0-5) 

```rust
fee_market_info: FeeMarketInfo {
    l2_gas_consumed: l2_gas_used,
    next_l2_gas_price: self.l2_gas_price,   // wrong
},
``` [7](#0-6) 

---

### Impact Explanation

The `next_l2_gas_price` field stored in `BlockHeaderWithoutHash` and broadcast in the cende blob is an authoritative value consumed by every downstream component (RPC fee estimation, the next proposer's starting price, the centralized recorder). Because the SNIP-35 `fee_actual` floor is computed one block behind, the stored `next_l2_gas_price` can be lower than the correct SNIP-35 floor when the current block's `fee_proposal_fri` is above the previous window's median, or higher than the correct floor when it is below. In either case the authoritative `next_l2_gas_price` diverges from the value mandated by the SNIP-35 spec, causing fee estimation and the fee market to operate on a wrong base price. This matches: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value**, and potentially **Critical — Incorrect fee/gas/resource accounting with economic impact** when the floor deviation is large enough to affect actual charged fees.

---

### Likelihood Explanation

This fires on every single block finalization after the SNIP-35 activation height (V0_14_3+). No special attacker capability is required; the bug

### Citations

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L61-80)
```rust
    let Some(start) = height.0.checked_sub(window_size) else {
        warn!(
            "Cannot compute fee_actual for height {height}: height is below window_size \
             ({window_size})"
        );
        return None;
    };
    let window_size_usize = usize::try_from(window_size).expect("window_size fits in usize");
    let mut window = Vec::with_capacity(window_size_usize);
    for source_height in (start..height.0).map(BlockNumber) {
        match fee_proposals_window.get(&source_height) {
            Some(Some(price)) => window.push(*price),
            Some(None) | None => {
                warn!(
                    "Cannot compute fee_actual for height {height}: fee_proposals_window has no \
                     recorded fee_proposal for height {source_height}"
                );
                return None;
            }
        }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L405-405)
```rust
            next_l2_gas_price: self.l2_gas_price,
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L427-441)
```rust
    fn calculate_next_l2_gas_price(&self, height: BlockNumber, l2_gas_used: GasAmount) -> GasPrice {
        let fee_actual = compute_fee_actual(
            &self.fee_proposals_window,
            height,
            VersionedConstants::latest_constants().fee_proposal_window_size,
        );
        calculate_next_l2_gas_price_for_fin(
            self.l2_gas_price,
            height,
            l2_gas_used,
            self.config.dynamic_config.override_l2_gas_price_fri,
            &self.config.dynamic_config.min_l2_gas_price_per_height,
            fee_actual,
        )
    }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L517-518)
```rust
        self.update_l2_gas_price(height, l2_gas_used);
        self.record_fee_proposal(height, init.fee_proposal_fri);
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L603-606)
```rust
                fee_market_info: FeeMarketInfo {
                    l2_gas_consumed: l2_gas_used,
                    next_l2_gas_price: self.l2_gas_price,
                },
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L782-786)
```rust
        let fee_actual = compute_fee_actual(
            &self.fee_proposals_window,
            build_param.height,
            VersionedConstants::latest_constants().fee_proposal_window_size,
        );
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L72-76)
```rust
    let effective_min = match fee_actual {
        Some(fa) => GasPrice(max(config_min.0, fa.0)),
        None => config_min,
    };
    calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
```
