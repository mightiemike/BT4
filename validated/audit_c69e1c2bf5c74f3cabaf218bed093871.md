### Title
Chainlink oracle in L1 gas price provider accepts circuit-breaker-clamped (minAnswer/maxAnswer) feed answers as genuine prices - ([File: crates/apollo_l1_gas_price/src/rate_bounds.rs])

### Summary
The sequencer's L1 gas price provider reads ETH/USD and STRK/USD Chainlink price feeds on-chain (via the batcher's `call_contract`) to compute the ETH↔FRI conversion rate used in fee and resource accounting for every block. The only sanity check applied to a feed's `answer` is a static, wide "plausibility" range (`RateBounds`) configured by the operator, not a check against Chainlink's own `minAnswer`/`maxAnswer` circuit-breaker bounds. If a Chainlink aggregator hits its internal circuit breaker during extreme market conditions, it keeps returning the frozen `minAnswer`/`maxAnswer` value, which is still well inside the wide operator-configured `RateBounds`, so the sequencer accepts it as the real, current price and uses it to compute fee conversion rates network-wide.

### Finding Description
`read_feed` in [1](#0-0)  reads `decimals` and `latest_round_data` from the configured Chainlink proxy feed, checks the answer is non-zero and checks staleness/future-dating of `updated_at`, then calls `check_rate_bounds`. The bounds check in [2](#0-1)  only rejects a rate if it falls outside `bounds.minimum_rate`/`bounds.maximum_rate`, which are wide "absolute sanity" ranges (e.g. STRK/USD accepts $0.0001–$10, per the code's own comment at [3](#0-2) ).

Unlike the recommendation in the referenced report, there is no comparison of the aggregator's returned answer against the aggregator's own `minAnswer`/`maxAnswer` circuit-breaker thresholds. When a Chainlink aggregator saturates at its internal bound (e.g., during a crash or de-peg), it continues returning that clamped value with a fresh `updated_at`, passing every guard in `read_feed` (non-zero, fresh, within the wide sanity bounds), and is accepted as `ValidRead` by `ChainlinkOracleClient` ( [4](#0-3) ). This rate is then used to derive `eth_to_fri_rate` ( [5](#0-4) ), which feeds `get_l1_prices_in_fri_and_wei_and_conversion_rate` and ultimately `apply_fee_transformations`/`convert_to_sn_api_block_info` ( [6](#0-5) ), which sets the `GasPrices` used for resource-bound checks and fee computation for every transaction in the block ( [7](#0-6) , [8](#0-7) ).

### Impact Explanation
Because both the proposer and validators independently read the same on-chain (clamped) Chainlink answer and cross-check each other only via a wide `l1_gas_price_margin_percent`/`within_margin` tolerance ( [9](#0-8) , [10](#0-9) ), a clamped-but-plausible price does not trigger consensus divergence or proposal rejection — it is uniformly accepted network-wide. This causes the derived L1↔L2 fee conversion rate to be systematically wrong (frozen at the aggregator's `minAnswer`/`maxAnswer`) for as long as the underlying asset stays beyond the aggregator's bound, silently mispricing L1 gas costs baked into every transaction's fee for the duration of the incident (analogous to the referenced report's "protocol incurring losses due to incorrect collateral pricing"), which is a concrete network-wide fee/resource-accounting miscalculation rather than a localized error.

### Likelihood Explanation
This requires no attacker-controlled transaction; it is triggered purely by an external market/circuit-breaker event on the underlying Chainlink aggregator (as documented to occur historically, e.g., during the LUNA crash referenced in the source report). The code's own comment in `rate_bounds.rs` explicitly acknowledges the configured bounds are "wide enough to pass a manipulated but plausible answer," confirming the gap is a known, currently-open risk (marked `TODO(Asaf)`), which raises the likelihood that a genuine aggregator saturation event would go undetected until the TODO's proposed relative-change bound is implemented.

### Recommendation
When reading `latest_round_data`, also read (or otherwise obtain) the aggregator's `minAnswer`/`maxAnswer` (or a configured proxy for them) and reject/flag a round whose `answer` equals or is within a small epsilon of those extremes, in addition to the existing sanity `RateBounds` check in `check_rate_bounds`. Alternatively/additionally, implement the already-flagged TODO to bound the rate's change relative to the previous accepted rate (anchored to block header state so all validators agree), which would independently catch a price "stuck" at a circuit-breaker bound over successive samples.

### Proof of Concept
Not applicable as a submitted-transaction PoC — the vulnerable condition is triggered by an external Chainlink aggregator saturating at its internal `minAnswer`/`maxAnswer` during extreme market movement, which is outside the control of an unprivileged sequencer sender; a PoC would require simulating a Chainlink feed contract that clamps `latest_round_data.answer` to its bound with a fresh `updated_at`, and observing that `read_feed`/`check_rate_bounds` ( [11](#0-10)  and [12](#0-11) ) accept it, propagating into `eth_to_fri_rate` and block fee pricing.

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

**File:** crates/apollo_l1_gas_price/src/rate_bounds.rs (L11-32)
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
}
```

**File:** crates/apollo_l1_gas_price/src/chainlink_oracle/mod.rs (L181-215)
```rust
    // Moves a finished query's outcome into `state`: a success becomes the last valid read and
    // clears the last error, a failure becomes the last error. Called on every
    // `fetch_rate`, so that a query which resolved after the last caller that could have observed
    // it is harvested rather than dropped together with the round trip that produced it.
    fn harvest_finished_query(&self, state: &mut PairOracleState) {
        if !state.query.as_ref().is_some_and(|query| query.is_finished()) {
            return;
        }
        let joined = state
            .query
            .take()
            .expect("Query must be present if it reported being finished")
            .now_or_never()
            .expect("Finished query must resolve immediately");
        let result = joined.unwrap_or_else(|join_error| {
            let error = ExchangeRateOracleClientError::JoinError(join_error.to_string());
            self.metrics.record_error((&error).into());
            warn!("Query failed to join its handle: {error:?}");
            Err(error)
        });
        match result {
            Ok(valid_read) => {
                debug!(
                    "Harvested {:?} rate {} for block timestamp {}",
                    Kind::PAIR,
                    valid_read.rate,
                    valid_read.block_timestamp
                );
                state.last_valid_read = Some(valid_read);
                state.last_error = None;
            }
            // `spawn_query` already warned; this only holds it for the retry interval.
            Err(error) => state.last_error = Some(error),
        }
    }
```

**File:** crates/apollo_l1_gas_price/src/chainlink_oracle/mod.rs (L283-303)
```rust
    async fn query_rate(
        batcher_client: &SharedBatcherClient,
        config: &ChainlinkOracleConfig,
        bounds_config: &AllRateBoundsConfig,
        block_timestamp: u64,
    ) -> RateResult {
        // The two legs are separate `call_contract` calls, which exposes no block pinning, so they
        // may straddle a block boundary. A one-block skew is orders of magnitude below the
        // staleness bound both legs must independently pass.
        // Sequential, not `try_join`: a failing leg would drop the other mid-flight, and the
        // batcher's local component server panics when a dropped request's response channel
        // closes. The same constraint applies to the two `call_view` calls inside `read_feed`.
        let eth_to_usd_rate =
            read_feed(batcher_client, config.eth_usd_feed(bounds_config), block_timestamp).await?;
        let strk_to_usd_rate =
            read_feed(batcher_client, config.strk_usd_feed(bounds_config), block_timestamp).await?;

        let eth_to_fri_rate = derive_eth_to_fri_rate(eth_to_usd_rate, strk_to_usd_rate)?;
        check_rate_bounds(eth_to_fri_rate, bounds_config.eth_strk_bounds())?;
        Ok(eth_to_fri_rate)
    }
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L135-181)
```rust
pub(crate) async fn get_l1_prices_in_fri_and_wei_and_conversion_rate(
    l1_gas_price_provider_client: Arc<dyn L1GasPriceProviderClient>,
    timestamp: u64,
    previous_proposal_init: Option<&PreviousProposalInitInfo>,
    gas_price_params: &GasPriceParams,
) -> (L1PricesInFri, L1PricesInWei, u128) {
    // One of these paths should fill the return values:
    // 1. Both L1 gas price and eth/strk rate are Ok, use those.
    // 2. Otherwise, use previous block info.
    // 3. If that isn't available either, use min gas prices and default eth/strk rate.

    // Get the eth to fri rate from the oracle, and the L1 gas price (in wei) from the provider.
    let (eth_to_fri_rate, price_info) = tokio::join!(
        l1_gas_price_provider_client.get_rate(timestamp),
        l1_gas_price_provider_client.get_price_info(BlockTimestamp(timestamp))
    );
    if price_info.is_err() {
        warn!("Failed to get l1 gas price from provider: {:?}", price_info);
        CONSENSUS_L1_GAS_PRICE_PROVIDER_ERROR.increment(1);
    }
    if eth_to_fri_rate.is_err() {
        warn!("Failed to get eth to fri rate from oracle: {:?}", eth_to_fri_rate);
    }
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
        } else {
            warn!(
                "Failed to convert L1 gas prices to FRI: {:?}",
                l1_gas_prices_fri_result.clone().err()
            );
        }
    }
```

**File:** crates/blockifier/src/fee/fee_utils.rs (L138-146)
```rust
/// Converts the gas vector to a fee.
pub fn get_fee_by_gas_vector(
    block_info: &BlockInfo,
    gas_vector: GasVector,
    fee_type: &FeeType,
    tip: Tip,
) -> Fee {
    gas_vector.cost(block_info.gas_prices.gas_price_vector(fee_type), tip)
}
```

**File:** crates/starknet_api/src/execution_resources.rs (L155-187)
```rust
    /// Computes the cost (in fee token units) of the gas vector (panicking on overflow).
    pub fn cost(&self, gas_prices: &GasPriceVector, tip: Tip) -> Fee {
        let tipped_l2_gas_price =
            gas_prices.l2_gas_price.checked_add(tip.into()).unwrap_or_else(|| {
                panic!(
                    "Tip overflowed: addition of L2 gas price ({}) and tip ({}) resulted in \
                     overflow.",
                    gas_prices.l2_gas_price, tip
                )
            });

        let mut sum = Fee(0);
        for (gas, price, resource) in [
            (self.l1_gas, gas_prices.l1_gas_price, Resource::L1Gas),
            (self.l1_data_gas, gas_prices.l1_data_gas_price, Resource::L1DataGas),
            (self.l2_gas, tipped_l2_gas_price, Resource::L2Gas),
        ] {
            let cost = gas.checked_mul(price.get()).unwrap_or_else(|| {
                panic!(
                    "{resource} cost overflowed: multiplication of gas amount ({gas}) by price \
                     per unit ({price}) resulted in overflow."
                )
            });
            sum = sum.checked_add(cost).unwrap_or_else(|| {
                panic!(
                    "Total cost overflowed: addition of current sum ({sum}) and cost of \
                     {resource} ({cost}) resulted in overflow."
                )
            });
        }
        sum
    }
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L312-351)
```rust
    let l1_gas_price_margin_percent =
        VersionedConstants::latest_constants().l1_gas_price_margin_percent.into();
    debug!("L1 price info: fri={l1_gas_prices_fri:?}, wei={l1_gas_prices_wei:?}");

    let l1_gas_price_fri = l1_gas_prices_fri.l1_gas_price;
    let l1_data_gas_price_fri = l1_gas_prices_fri.l1_data_gas_price;
    let l1_gas_price_wei = l1_gas_prices_wei.l1_gas_price;
    let l1_data_gas_price_wei = l1_gas_prices_wei.l1_data_gas_price;
    let l1_gas_price_fri_proposed = init_proposed.l1_gas_price_fri;
    let l1_data_gas_price_fri_proposed = init_proposed.l1_data_gas_price_fri;
    let l1_gas_price_wei_proposed = init_proposed.l1_gas_price_wei;
    let l1_data_gas_price_wei_proposed = init_proposed.l1_data_gas_price_wei;

    if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, l1_gas_price_margin_percent)
        && within_margin(
            l1_data_gas_price_fri_proposed,
            l1_data_gas_price_fri,
            l1_gas_price_margin_percent,
        )
        && within_margin(l1_gas_price_wei_proposed, l1_gas_price_wei, l1_gas_price_margin_percent)
        && within_margin(
            l1_data_gas_price_wei_proposed,
            l1_data_gas_price_wei,
            l1_gas_price_margin_percent,
        ))
    {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "L1 gas price mismatch: expected L1 gas price FRI={l1_gas_price_fri}, \
                 proposed={l1_gas_price_fri_proposed}, expected L1 data gas price \
                 FRI={l1_data_gas_price_fri}, proposed={l1_data_gas_price_fri_proposed}, expected \
                 L1 gas price WEI={l1_gas_price_wei}, proposed={l1_gas_price_wei_proposed}, \
                 expected L1 data gas price WEI={l1_data_gas_price_wei}, \
                 proposed={l1_data_gas_price_wei_proposed}, \
                 l1_gas_price_margin_percent={l1_gas_price_margin_percent}"
            ),
        ));
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L404-414)
```rust
fn within_margin(number1: GasPrice, number2: GasPrice, margin_percent: u128) -> bool {
    // For small numbers (e.g., less than 10 wei, if margin is 10%), even an off-by-one
    // error might be bigger than the margin, even if it is just a rounding error.
    // We make an exception for such mismatch, and don't bother checking percentages
    // if the difference in price is only one wei.
    if number1.0.abs_diff(number2.0) <= GAS_PRICE_ABS_DIFF_MARGIN {
        return true;
    }
    let margin = (number1.0 * margin_percent) / 100;
    number1.0.abs_diff(number2.0) <= margin
}
```
