### Title
Inaccurate ETH/STRK exchange rate from oracle-leg depeg passes only a wide static absolute-bound check, corrupting L1→FRI fee conversion and SNIP-35 fee target - ([File: crates/apollo_l1_gas_price/src/rate_bounds.rs])

### Summary
The sequencer derives the ETH/STRK exchange rate (used to price every transaction's L1 gas cost in FRI, and to compute the SNIP-35 L2 fee target) by dividing two independently-sourced USD legs (ETH/USD and STRK/USD). Each leg, and the resulting derived rate, is validated only against a fixed, wide absolute min/max bound, with no cross-check between the legs or against the previously accepted rate. If either leg reports a plausible-but-depegged value (analogous to a USDC depeg skewing a Chainlink USD-quoted ratio), the derived rate can pass the bound check while still being materially wrong, exactly the failure mode described in the referenced report.

### Finding Description
`derive_eth_to_fri_rate` computes the ETH/STRK rate purely as a division of the two USD legs read from Chainlink feeds: [1](#0-0) 

Each leg is individually staleness-checked, then the composed rate is checked only against a static absolute bound: [2](#0-1) 

The bound-check function itself, and its accompanying comment, explicitly acknowledge that the absolute bounds are wide enough to accept a "manipulated but plausible answer" and that only a bound relative to the previously accepted rate would catch it — this protection is a documented `TODO`, not yet implemented: [3](#0-2) 

This derived (or STRK/USD) rate is not an isolated metric — it is consumed on the hot path of every proposed block:
1. To convert L1 gas prices from wei to FRI for every transaction's L1 data/gas fee: [4](#0-3) 
2. To compute the SNIP-35 dynamic L2 gas fee target, which is a straight division by `strk_usd_rate`: [5](#0-4) 
3. Both feed into the proposer's per-block fee proposal, published as part of `ConsensusBlockInfo`: [6](#0-5) 

Because one leg of the ratio (e.g. the STRK/USD "quote" leg, comment-documented as accepting anywhere from $0.0001 to $10) can swing by orders of magnitude while still passing its own bound and while the composed ETH/STRK rate can still land inside its own wide absolute bound, a depeg-class event in either leg's underlying price feed silently corrupts the derived rate used to price every transaction's fee, with no leg-to-leg consistency check and no anchoring to the previously accepted rate.

### Impact Explanation
An inaccurate ETH/STRK (or STRK/USD) rate is used unconditionally in the fee-computation path reachable by any submitted transaction: it directly determines the L1 gas price in FRI charged for every transaction's L1 data/gas cost, and the SNIP-35 L2 gas fee target every proposer publishes. A materially wrong rate that still clears the static absolute bound (the very scenario the code's own `TODO` comment calls out) causes users to be over- or under-charged fees network-wide, and skews the resource accounting figures (`l1_gas_price_fri`, `l2_gas_price_fri`) committed into `ConsensusBlockInfo`, which participates in block hash/commitment computation. This is a fee-and-resource-accounting correctness bug reachable purely by the passage of time/blocks (no privileged actor needed), and results in concrete economic loss (over/undercharging) borne on every transaction priced during the affected window.

### Likelihood Explanation
This does not require any malicious operator, proposer, or peer — it only requires one of the two independently-sourced USD legs to move sharply (a real-world depeg or a Chainlink feed anomaly) while remaining within its own generously wide absolute bound, which the code's inline comment explicitly acknowledges is possible today. The bound is a fixed, sampling-interval-scoped, config-level check with no leg cross-validation or prior-rate anchoring, so the condition can occur under ordinary (non-adversarial) market stress.

### Recommendation
Add a bound on the derived rate's change relative to the last accepted rate (as already flagged by the `TODO(Asaf)` comment in `rate_bounds.rs`), anchored to a value all validators can independently reproduce (e.g., the previous block's implied rate), rather than relying solely on static absolute min/max bounds per pair.

### Proof of Concept
1. STRK/USD Chainlink feed reports a value that has moved sharply from its true market price but remains within the configured absolute bound (`$0.0001`–`$10`, i.e. a >100x range) — e.g., from $0.03 to $0.003, a 10x move, comparable to a stablecoin-leg depeg in the referenced report.
2. ETH/USD feed reports normally (unchanged, fresh, within bounds).
3. `derive_eth_to_fri_rate` computes a derived ETH/STRK rate that is ~10x off from the true market rate: `crates/apollo_l1_gas_price/src/chainlink_oracle/feed_math.rs:32-52`.
4. `check_rate_bounds` on the composed rate passes because the eth_strk absolute bound window is wide enough to still contain the skewed value: `crates/apollo_l1_gas_price/src/rate_bounds.rs:19-31`.
5. The skewed rate is consumed by `get_l1_prices_in_fri_and_wei_and_conversion_rate` to convert L1 wei prices into FRI (`crates/apollo_consensus_orchestrator/src/utils.rs:170-171`) and by `compute_fee_target`/`resolve_fee_target` to compute the SNIP-35 fee target (`crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs:102-113`, `crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs:447-470`), causing every transaction in the affected blocks to be priced against a materially wrong rate.

### Citations

**File:** crates/apollo_l1_gas_price/src/chainlink_oracle/feed_math.rs (L32-52)
```rust
pub(super) fn derive_eth_to_fri_rate(
    eth_to_usd_rate: ExchangeRate,
    strk_to_usd_rate: ExchangeRate,
) -> RateResult {
    if strk_to_usd_rate == 0 {
        return Err(ExchangeRateOracleClientError::ArithmeticError(
            "deriving ETH/STRK from a zero strk_to_usd_rate".to_string(),
        ));
    }
    // The division cancels the two operands' scales, so the numerator is scaled back up by
    // `EXCHANGE_RATE_SCALE` first. U256 because that product overflows u128 for any realistic ETH
    // price; the quotient is back within u128 whenever the two legs are within their bounds.
    let scaled_rate = (U256::from(eth_to_usd_rate) * U256::from(EXCHANGE_RATE_SCALE))
        / U256::from(strk_to_usd_rate);
    ExchangeRate::try_from(scaled_rate).map_err(|_| {
        ExchangeRateOracleClientError::ArithmeticError(format!(
            "deriving ETH/STRK from eth_to_usd_rate={eth_to_usd_rate} and \
             strk_to_usd_rate={strk_to_usd_rate} overflowed"
        ))
    })
}
```

**File:** crates/apollo_l1_gas_price/src/chainlink_oracle/mod.rs (L295-303)
```rust
        let eth_to_usd_rate =
            read_feed(batcher_client, config.eth_usd_feed(bounds_config), block_timestamp).await?;
        let strk_to_usd_rate =
            read_feed(batcher_client, config.strk_usd_feed(bounds_config), block_timestamp).await?;

        let eth_to_fri_rate = derive_eth_to_fri_rate(eth_to_usd_rate, strk_to_usd_rate)?;
        check_rate_bounds(eth_to_fri_rate, bounds_config.eth_strk_bounds())?;
        Ok(eth_to_fri_rate)
    }
```

**File:** crates/apollo_l1_gas_price/src/rate_bounds.rs (L1-32)
```rust
//! The absolute sanity bounds every exchange rate must fall in, whichever source reports it.

use apollo_l1_gas_price_config::config::RateBounds;
use apollo_l1_gas_price_types::errors::ExchangeRateOracleClientError;
use apollo_l1_gas_price_types::ExchangeRate;

#[cfg(test)]
#[path = "rate_bounds_test.rs"]
mod rate_bounds_test;

// TODO(Asaf): bound the rate's change against the previous block's implied rate. The absolute
// bounds below are wide enough to pass a manipulated but plausible answer, the STRK/USD pair alone
// accepting anything from $0.0001 to $10, which only a bound relative to the last accepted rate
// catches. It must be anchored to the block header rather than to node-local history, so that every
// validator accepts and rejects the same values.
/// Absolute bounds are the only defense against a feed wired to the wrong asset or a
/// plausible-but-poisoned answer: consensus checks that validators agree with each other, never
/// that the agreed value is sane, and every node reads the same chain state.
pub(crate) fn check_rate_bounds(
    rate: ExchangeRate,
    bounds: RateBounds,
) -> Result<(), ExchangeRateOracleClientError> {
    if rate < bounds.minimum_rate || rate > bounds.maximum_rate {
        return Err(ExchangeRateOracleClientError::RateOutOfBoundsError {
            pair: bounds.pair,
            rate,
            min_rate: bounds.minimum_rate,
            max_rate: bounds.maximum_rate,
        });
    }
    Ok(())
}
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L158-174)
```rust
    if let (Ok(eth_to_fri_rate), Ok(mut price_info)) = (eth_to_fri_rate, price_info) {
        // Both L1 prices and rate are Ok, so we can use them.
        info!(
            "raw eth_to_fri_rate (from oracle): {eth_to_fri_rate}, raw l1 gas price wei (from \
             provider): {price_info:?}"
        );
        apply_fee_transformations(&mut price_info, gas_price_params);
        let prices_in_wei = L1PricesInWei {
            l1_gas_price: price_info.base_fee_per_gas,
            l1_data_gas_price: price_info.blob_fee,
        };
        // Apply the eth/strk rate to get prices in fri.
        let l1_gas_prices_fri_result =
            L1PricesInFri::convert_from_wei(&prices_in_wei, eth_to_fri_rate);
        // If conversion fails, leave return_value=None to try backup methods.
        if let Ok(prices_in_fri) = l1_gas_prices_fri_result {
            return (prices_in_fri, prices_in_wei, eth_to_fri_rate);
```

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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L447-498)
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

    /// Compute the proposer's fee_proposal: clamp the oracle's `fee_target` to a margin around
    /// `fee_actual`. When `fee_actual` is `None` (window incomplete), freeze at `l2_gas_price`; the
    /// validator derives the same fallback so both sides agree.
    async fn compute_proposer_fee_proposal(
        &self,
        fee_actual: Option<GasPrice>,
        timestamp: u64,
        target_atto_usd_per_l2_gas: u128,
    ) -> GasPrice {
        SNIP35_FEE_TARGET_ATTO_USD.set_lossy(target_atto_usd_per_l2_gas);
        let Some(fee_actual) = fee_actual else {
            warn!("fee_actual unavailable, freezing fee_proposal at l2_gas_price");
            SNIP35_FEE_PROPOSAL_FRI.set_lossy(self.l2_gas_price.0);
            return self.l2_gas_price;
        };
        SNIP35_FEE_ACTUAL_FRI.set_lossy(fee_actual.0);

        let fee_target = self.resolve_fee_target(timestamp, target_atto_usd_per_l2_gas).await;

        let proposal = compute_fee_proposal(
            fee_target,
            fee_actual,
            VersionedConstants::latest_constants().fee_proposal_margin_ppt,
        );
        SNIP35_FEE_PROPOSAL_FRI.set_lossy(proposal.0);
        proposal
    }
```
