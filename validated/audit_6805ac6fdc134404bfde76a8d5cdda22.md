Based on my research, I have enough evidence to write up the analog finding.

### Title
SNIP-35 fee proposal's per-block margin clamp cannot track fast real-world STRK/USD price moves, causing sustained fee under-collection - (File: crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs)

### Summary
The L2 gas price in FRI is driven toward a USD-pegged target (`compute_fee_target`) derived from a live STRK/USD oracle rate, but the published `fee_proposal`/`fee_actual` that actually floors the charged gas price is only allowed to move by a fixed geometric margin (`fee_proposal_margin_ppt`, e.g. 2‰ = 0.2%) per block via `compute_fee_proposal`/`fee_proposal_bounds`. This mirrors the Surge `Pool.sol` pattern of a linear/bounded-step cRatio that cannot keep pace with non-linear real-world price moves, letting the effective exchange rate go stale.

### Finding Description
`compute_fee_target` converts a fixed USD-per-gas target into FRI using the oracle's `strk_usd_rate` every block [1](#0-0) . However, the actual published `fee_proposal` clamps this fresh target into a narrow band around the previous `fee_actual`, controlled by `fee_proposal_margin_ppt` (2 parts-per-thousand in the current versioned constants) [2](#0-1) [3](#0-2) . This `fee_actual` is then used as a floor for the EIP-1559-style L2 base gas price update (`snip35_min`/`effective_min`) that every accepted transaction is charged against [4](#0-3) .

A test in the codebase itself demonstrates the staleness: a 33% jump in the target fee takes ~800 blocks to converge, and a 40% drop takes ~1420 blocks [5](#0-4) . Real-world STRK/USD price changes (e.g., a fast STRK depreciation) are not bound to a 0.2%-per-block linear ramp; if STRK loses value faster than the margin allows the FRI-denominated price to rise, the network will charge transactions in FRI at a rate that undervalues the actual USD/ETH-denominated cost the sequencer must cover for L1 data-posting, for potentially hundreds to over a thousand blocks.

### Impact Explanation
During a rapid real-world STRK depreciation (or a comparably fast move in the opposite direction, undercutting revenue vs. cost), any unprivileged transaction sender can submit transactions priced by `l2_gas_price`, which is pinned near the stale `fee_actual`/`snip35_min` floor rather than the fresh oracle-derived target for the full convergence window. This causes systematic fee under-collection relative to the sequencer's real L1-denominated costs across that window — a sustained value transfer away from the protocol/network, analogous to the "loss of funds to pool liquidity providers" impact in the reference report. This does not require any malicious operator, prover, or peer — it is purely a consequence of a normal user submitting ordinary transactions while the oracle-tracked target diverges quickly from the bounded ramp.

### Likelihood Explanation
STRK/USD volatility episodes (e.g. >10-40% moves within a few hours) are realistic market events, and the fee proposal mechanism enforces only a fixed 0.2%-per-block (per current constants) convergence rate regardless of how sharply the oracle-derived target has moved, as shown by the multi-hundred-block convergence windows demonstrated in the repo's own tests [6](#0-5) . No special positioning or privileged role is needed to benefit from the underpriced window — any ordinary transaction submitted during it benefits from the stale price.

### Recommendation
Consider widening the margin dynamically based on the magnitude/velocity of the oracle-derived target's divergence from `fee_actual` (e.g., an adaptive or non-linear catch-up rate, similar to curve-based approaches), or allow a fast-path re-anchoring when the oracle target and `fee_actual` diverge beyond a large threshold, so the charged FRI price cannot lag a genuine market move for over a thousand blocks.

### Proof of Concept
1. Establish a stable `fee_actual`/`l2_gas_price` under normal STRK/USD conditions.
2. Have the STRK/USD oracle rate move sharply (simulating a real market crash), which `resolve_fee_target`/`compute_fee_target` immediately reflects in `fee_target` [7](#0-6) .
3. Because `compute_fee_proposal` clamps to `fee_proposal_bounds(fee_actual, margin_ppt)` each block, `fee_actual`/`l2_gas_price` only creeps toward `fee_target` by the fixed geometric margin per block [8](#0-7) .
4. As demonstrated in `test_compute_proposer_fee_proposal_converges_to_oracle_target`, a 40% real-world move requires ~1420 blocks to fully converge [5](#0-4) ; during that entire window, ordinary transactions are charged at the stale, mispriced rate.

### Citations

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L102-113)
```rust
pub fn compute_fee_target(
    target_atto_usd_per_l2_gas: u128,
    strk_usd_rate: u128,
) -> Option<GasPrice> {
    if strk_usd_rate == 0 {
        return None;
    }
    // floor_fri = target_atto_usd_per_l2_gas * 10^18 / strk_usd_rate
    let numerator = U256::from(target_atto_usd_per_l2_gas) * U256::from(FRI_DECIMALS_SCALE);
    let floor = numerator / U256::from(strk_usd_rate);
    Some(GasPrice(u128::try_from(floor).unwrap_or(u128::MAX)))
}
```

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L115-151)
```rust
/// Compute the fee_proposal an honest proposer should publish.
/// - If oracle failed (`fee_target` is `None`): freeze at `fee_actual`.
/// - Otherwise: clamp `fee_target` into the geometric bounds returned by `fee_proposal_bounds`.
pub fn compute_fee_proposal(
    fee_target: Option<GasPrice>,
    fee_actual: GasPrice,
    margin_ppt: u128,
) -> GasPrice {
    let Some(fee_target) = fee_target else {
        return fee_actual;
    };
    let (lower, upper) = fee_proposal_bounds(fee_actual, margin_ppt);
    GasPrice(fee_target.0.clamp(lower, upper))
}

/// Geometric bounds for fee_proposal: returns `(lower, upper)` where
/// - `upper = fee_actual * (1 + margin)` (multiplicative widening), and
/// - `lower = fee_actual / (1 + margin)` (the reciprocal — multiplicative narrowing),
///
/// with `margin = margin_ppt / PPT_DENOMINATOR`.
///
/// The asymmetry is intentional: bounds are geometrically symmetric (the same
/// multiplicative factor in either direction), so a sequence of consecutive proposals
/// can grow by `(1 + margin)` per round or shrink by the same factor per round. Both
/// proposer and validator use this helper to ensure they agree on what's in-range.
///
/// Uses `U256` internally to keep the arithmetic mathematically correct regardless of
/// `fee_actual` and `margin_ppt`. On the practically-unreachable overflow, the upper
/// bound saturates to `u128::MAX` and the lower bound saturates to `0`.
pub(crate) fn fee_proposal_bounds(fee_actual: GasPrice, margin_ppt: u128) -> (u128, u128) {
    let denom = U256::from(PPT_DENOMINATOR);
    let scaled = denom + U256::from(margin_ppt);
    let fee_actual_u256 = U256::from(fee_actual.0);
    let upper = u128::try_from(fee_actual_u256 * scaled / denom).unwrap_or(u128::MAX);
    let lower = u128::try_from(fee_actual_u256 * denom / scaled).unwrap_or(0);
    (lower, upper)
}
```

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_0.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 3200000000,
    "max_block_size": 4000000000,
    "min_gas_price": "0xb2d05e00",
    "l1_gas_price_margin_percent": 10
}
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L90-97)
```rust
    let config_min = get_min_gas_price_for_height(height, min_l2_gas_price_per_height);
    let cap = l2_gas_price_cap(config_min);

    let snip35_min = fee_actual.map_or(config_min, |fee_actual| max(config_min, fee_actual));
    let effective_min = min(snip35_min, cap);

    let raw_price =
        calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min);
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context_test.rs (L1838-1888)
```rust
#[tokio::test]
async fn test_compute_proposer_fee_proposal_converges_to_oracle_target() {
    // (strk_usd_rate, fee_target, n_blocks_until_convergence_with_buffer).
    // 75 gwei bootstrap -> 100 gwei target at +33% reaches by block ~795.
    // 100 gwei -> 60 gwei at -40% reaches by block ~1410.
    let phases: [(u128, GasPrice, u64); 2] = [
        (30_000_000_000_000_000, GasPrice(100_000_000_000), 800),
        (50_000_000_000_000_000, GasPrice(60_000_000_000), 1420),
    ];

    let (mut deps, _network) = create_test_and_network_deps();
    // Register per-phase strk_to_usd_rate expectations BEFORE setup_default_expectations so they
    // are not shadowed by the catch-all default (mockall matches oldest first).
    let mut seq = mockall::Sequence::new();
    for &(rate, _, n_blocks) in &phases {
        deps.l1_gas_price_provider
            .expect_get_strk_to_usd_rate()
            .times(usize::try_from(n_blocks).unwrap())
            .in_sequence(&mut seq)
            .returning(move |_| Ok(rate));
    }
    deps.setup_default_expectations();
    let mut context = deps.build_context();

    // Bootstrap the window with 75 gwei (the $0.04 target).
    let window_size = VersionedConstants::latest_constants().fee_proposal_window_size;
    for h in 0..window_size {
        context.record_fee_proposal(BlockNumber(h), Some(GasPrice(75_000_000_000)));
    }

    let mut height = window_size;
    for (phase_idx, (_, fee_target, n_blocks)) in phases.into_iter().enumerate() {
        for _ in 0..n_blocks {
            let h = BlockNumber(height);
            let fee_actual = compute_fee_actual(&context.fee_proposals_window, h, window_size)
                .expect("window stays complete across the loop");
            let proposal = context
                .compute_proposer_fee_proposal(Some(fee_actual), 0, TARGET_ATTO_USD_PER_L2_GAS)
                .await;
            context.record_fee_proposal(h, Some(proposal));
            height += 1;
        }
        let final_fee_actual =
            compute_fee_actual(&context.fee_proposals_window, BlockNumber(height), window_size)
                .expect("window stays complete across the loop");
        assert_eq!(
            final_fee_actual, fee_target,
            "phase {phase_idx}: fee_actual did not reach fee_target after {n_blocks} blocks",
        );
    }
}
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L447-470)
```rust
    async fn resolve_fee_target(
        &self,
        timestamp: u64,
        target_atto_usd_per_l2_gas: u128,
    ) -> Option<GasPrice> {
        if let Some(v) = self.config.dynamic_config.override_l2_gas_price_fri {
            SNIP35_FEE_TARGET_FRI.set_lossy(v);
            return Some(GasPrice(v));
        }
        match self.deps.l1_gas_price_provider.get_strk_to_usd_rate(timestamp).await {
            Ok(rate) => {
                let target = compute_fee_target(target_atto_usd_per_l2_gas, rate);
                match target {
                    Some(t) => SNIP35_FEE_TARGET_FRI.set_lossy(t.0),
                    None => warn!("STRK/USD oracle returned zero rate, freezing fee_proposal"),
                }
                target
            }
            Err(e) => {
                warn!("STRK/USD oracle error: {e:?}, freezing fee_proposal");
                None
            }
        }
    }
```
