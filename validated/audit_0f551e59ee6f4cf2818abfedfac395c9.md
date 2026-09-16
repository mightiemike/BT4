### Title
Chainlink price-feed circuit breaker can be masked by overly wide absolute sanity bounds, letting the sequencer accept a stale/incorrect ETH↔STRK rate - (File: `crates/apollo_l1_gas_price/src/rate_bounds.rs`)

### Summary
The sequencer's `ChainlinkOracleClient` reads Chainlink's on-chain `ETH/USD` and `STRK/USD` price feeds through the batcher and only validates each answer against a static, very wide absolute range (`check_rate_bounds`), exactly the "check min/max" mitigation the external report recommends. However, the bounds are wide enough (e.g. STRK/USD accepts $0.0001–$10, a five order-of-magnitude window) that a Chainlink aggregator hitting its own internal circuit-breaker `minAnswer`/`maxAnswer` and freezing at a stale price would still pass the sequencer's sanity check, producing a wrong-but-plausible `ETH→FRI` conversion rate used to price every L1 gas fee for the network.

### Finding Description
`read_feed` in [1](#0-0)  reads `decimals` and `latest_round_data` from the configured Chainlink proxy feed, rescales the answer, and calls `check_rate_bounds` as the only sanity check on the reported price. `check_rate_bounds` is a pure absolute-range check with no reference to the previously accepted rate: [2](#0-1) 

The code comment explicitly documents that this is insufficient: the bounds are "wide enough to pass a manipulated but plausible answer... which only a bound relative to the last accepted rate catches", and a `TODO` marks the missing deviation check as not yet implemented. The default bounds configured in `AllRateBoundsConfig::default()` are order-of-magnitude wide (e.g. STRK/USD $0.0001–$10, ETH/USD $20–$50,000): [3](#0-2) 

This mirrors the reported Chainlink bug class precisely: if the underlying aggregator's internal circuit breaker clamps `answer` to its own `minAnswer`/`maxAnswer` during an extreme market move (a crash or spike), the frozen value is very likely still inside the sequencer's much wider absolute band, so `check_rate_bounds` cannot detect that the feed has stopped tracking the real market price. Nothing else in the read path (freshness checks in the same function only bound `updated_at` staleness, not price plausibility) provides a deviation-from-recent-rate check.

### Impact Explanation
The `ETH→STRK` rate derived from these feeds (`derive_eth_to_fri_rate` in `chainlink_oracle/mod.rs`) is consumed by `apollo_consensus_orchestrator` to convert L1 gas/data-gas prices from WEI to FRI for every block proposal: [4](#0-3) 

A frozen/incorrect-but-in-bounds rate would be committed into every block's `ConsensusBlockInfo` (`l1_gas_price_fri`, `l1_data_gas_price_fri`), systemically mispricing L1 costs charged to users network-wide for as long as the aggregator remains clamped. This is a resource/fee-accounting correctness issue reachable purely from the normal proposal-building path (no privileged actor required) rather than a fund-freezing or state-divergence bug, since all honest sequencers query the same on-chain feed and would (barring the acknowledged non-determinism note in `ChainlinkOracleClient`'s doc comment) converge on the same wrong rate rather than diverge.

### Likelihood Explanation
Requires an external event (an L1 market crash/spike hitting the underlying Chainlink aggregator's own circuit-breaker band) rather than an attacker-controlled transaction, but is fully reachable through the normal, unprivileged block-building flow once that condition occurs — no operator or validator malice needed. The code's own `TODO` comment confirms the maintainers are already aware this specific gap (missing relative-deviation bound) exists and is unmitigated.

### Recommendation
Implement the deviation check the code comment describes: bound each accepted rate's change relative to the last accepted (or block-header-anchored) rate, in addition to the absolute sanity bounds, so a feed frozen at its aggregator's circuit-breaker limit is rejected rather than treated as a fresh, valid market read.

### Proof of Concept
1. Assume the on-chain STRK/USD or ETH/USD Chainlink aggregator has an internal `minAnswer`/`maxAnswer` circuit breaker (as all standard Chainlink aggregators do).
2. The underlying asset's real market price moves outside that internal band (e.g., a crash); the aggregator's `latest_round_data()` continues returning the clamped `minAnswer`/`maxAnswer` with a fresh `updated_at`.
3. `read_feed` (`crates/apollo_l1_gas_price/src/chainlink_oracle/feed_read.rs:64-108`) passes the freshness checks (the round is still being updated) and `check_rate_bounds` (`crates/apollo_l1_gas_price/src/rate_bounds.rs:19-31`) succeeds because the clamped value still sits inside the wide default absolute bounds.
4. The stale/incorrect rate is used by `apollo_consensus_orchestrator::utils::get_l1_prices_in_fri_and_wei_and_conversion_rate` to compute `l1_gas_price_fri`/`l1_data_gas_price_fri` for every subsequent block until the aggregator's circuit breaker resets, systemically mispricing L1 gas costs network-wide.

### Citations

**File:** crates/apollo_l1_gas_price/src/chainlink_oracle/feed_read.rs (L63-108)
```rust
/// The feed's answer, rescaled to `EXCHANGE_RATE_DECIMALS` and checked against the feed's bounds.
pub(super) async fn read_feed(
    batcher_client: &SharedBatcherClient,
    feed: PairFeed,
    block_timestamp: u64,
) -> RateResult {
    let pair = feed.bounds.pair;
    let pair_name = pair.pair_name();
    let feed_address = feed.feed_address;
    // `decimals` is read with every rate rather than cached: a feed that changes it rescales the
    // answer by a power of ten, which the absolute bounds are too wide to catch.
    let decimals_retdata = call_view(batcher_client, feed_address, DECIMALS_ENTRY_POINT).await?;
    let round_retdata =
        call_view(batcher_client, feed_address, LATEST_ROUND_DATA_ENTRY_POINT).await?;
    let feed_decimals = decode_feed_decimals(decimals_retdata, pair)?;

    let round = decode_feed_round(round_retdata)?;
    if round.answer == 0 {
        return Err(ExchangeRateOracleClientError::InvalidRateError(format!(
            "{pair_name} returned a zero answer"
        )));
    }
    if block_timestamp.saturating_sub(round.updated_at) > feed.freshness.max_staleness_seconds {
        return Err(ExchangeRateOracleClientError::StaleFeedError {
            pair,
            updated_at: round.updated_at,
            block_timestamp,
            max_staleness_seconds: feed.freshness.max_staleness_seconds,
        });
    }
    // Catches a round dated ahead of the block being priced: the staleness check above saturates
    // such a subtraction to zero, which alone treats it as fresh regardless of age.
    if round.updated_at.saturating_sub(block_timestamp)
        > feed.freshness.max_future_updated_at_seconds
    {
        return Err(ExchangeRateOracleClientError::FutureFeedError {
            pair,
            updated_at: round.updated_at,
            block_timestamp,
            max_future_updated_at_seconds: feed.freshness.max_future_updated_at_seconds,
        });
    }

    let rate = rescale_to_rate_decimals(round.answer, feed_decimals)?;
    check_rate_bounds(rate, feed.bounds)?;
    Ok(rate)
```

**File:** crates/apollo_l1_gas_price/src/rate_bounds.rs (L11-31)
```rust
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
```

**File:** crates/apollo_l1_gas_price_config/src/config.rs (L201-222)
```rust
impl Default for AllRateBoundsConfig {
    fn default() -> Self {
        const MICRO_UNITS_PER_UNIT: u64 = 10u64.pow(RATE_MICRO_UNIT_DECIMALS);

        Self {
            // $20 .. $50,000 per ETH, ~10x above the all-time high.
            eth_usd: RateBoundsConfig {
                minimum_micro_units: 20 * MICRO_UNITS_PER_UNIT,
                maximum_micro_units: 50_000 * MICRO_UNITS_PER_UNIT,
            },
            // $0.0001 .. $10 per STRK.
            strk_usd: RateBoundsConfig {
                minimum_micro_units: MICRO_UNITS_PER_UNIT / 10_000,
                maximum_micro_units: 10 * MICRO_UNITS_PER_UNIT,
            },
            // 10,000 .. 1,000,000 STRK per ETH, roughly 10x either side of spot near 8.2e4.
            eth_strk: RateBoundsConfig {
                minimum_micro_units: 10_000 * MICRO_UNITS_PER_UNIT,
                maximum_micro_units: 1_000_000 * MICRO_UNITS_PER_UNIT,
            },
        }
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
