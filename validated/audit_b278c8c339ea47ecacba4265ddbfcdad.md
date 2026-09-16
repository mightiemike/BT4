### Title
Chainlink oracle `max_staleness_seconds` default (~25h) is far too permissive for ETH/STRK price feeds, enabling stale gas-price data in block building and validator divergence - (File: crates/apollo_l1_gas_price_config/src/config.rs)

### Summary
The `ChainlinkOracleConfig::default()` sets `max_staleness_seconds` (the freshness window used to accept a Chainlink `updated_at` round) to `(24 + 1) * 3600` = 90,000 seconds (~25 hours) for both the ETH/USD and STRK/USD feeds that back the sequencer's ETH→STRK conversion rate. This mirrors exactly the `HEARTBEAT_TIME = 24 hours` flaw from the referenced report: a heartbeat/staleness bound sized for a slow-moving feed (the report's sUSD feed) applied uniformly to fast-moving feeds (ETH/USD), where real production heartbeats are typically far shorter (the report itself notes ETH/USD and OP/USD feeds on Optimism have ~20-minute heartbeats).

### Finding Description
The default freshness window is defined here: [1](#0-0) 

`max_staleness_seconds` gates `read_feed`, which is the sole staleness guard applied to every Chainlink round read by the sequencer before it is used to compute the ETH↔STRK exchange rate: [2](#0-1) 

This rate is not a side channel — it is consumed on every proposal build/validate cycle to convert L1 wei gas prices into the FRI prices that become part of `ConsensusBlockInfo` / `BlockInfo.gas_prices` (i.e., committed block data): [3](#0-2) [4](#0-3) 

The code's own documentation acknowledges that reads are not deterministic across nodes (each node queries the batcher independently and may land on a different Chainlink round for the same block timestamp), and relies on `l1_gas_price_margin_percent` (10%) to absorb this divergence: [5](#0-4) [6](#0-5) 

The validator-side check enforcing this margin is: [7](#0-6) 

The design's implicit safety assumption is that any two honestly-read Chainlink rounds for the same pair will differ by far less than 10%, because Chainlink's own on-chain deviation threshold bounds how much price can move between rounds. That assumption only holds if `max_staleness_seconds` is close to the feed's *actual* production heartbeat. By using a 25-hour staleness bound for ETH/USD (a feed whose actual Starknet/L2 heartbeat, per the referenced report's own examples for comparable ETH/USD feeds, is on the order of minutes to roughly an hour), the sequencer will accept a round that is up to 25 hours stale as "fresh." During normal ETH volatility, price can move well beyond the 10% `l1_gas_price_margin_percent` tolerance over a 25-hour window, so a proposer using a stale round and a validator using a fresh round (or vice versa) can legitimately diverge past the configured margin.

### Impact Explanation
Because the ETH→STRK rate feeds directly into the `l1_gas_price_fri` / `l1_data_gas_price_fri` fields that must match within `l1_gas_price_margin_percent` between proposer and validators, an over-wide staleness window makes it plausible for honest nodes — with no malicious behavior involved — to independently read valid-but-differently-staled rounds whose derived rates diverge by more than 10% during ordinary market volatility. This causes validators to reject otherwise-valid proposals (`InvalidProposalInit`/"L1 gas price mismatch"), which can repeatedly stall proposal acceptance across a round, degrading or halting the network's ability to confirm new blocks/transactions. This qualifies as a network liveness/availability impact reachable purely through the normal block-building and block-validation path (no special privileges required to trigger — it manifests under organic price movement).

### Likelihood Explanation
Likelihood is tied to ordinary crypto market volatility, not to any adversarial action: ETH/USD or STRK/USD moving more than 10% within a 25-hour window is a common occurrence, especially during periods of stress. The 25-hour window is also inherited unmodified across chains/pairs by default (`ChainlinkOracleConfig::default()`), so any deployment relying on defaults is exposed. The bug is a straightforward configuration-of-constants issue rather than a complex exploit chain, making it easy to trigger unintentionally.

### Recommendation
Tighten `max_staleness_seconds` in `ChainlinkOracleConfig::default()` to reflect the actual heartbeat of the specific ETH/USD and STRK/USD feeds deployed on Starknet (query each feed's on-chain heartbeat/deviation parameters rather than assuming a uniform 24h value), and/or tie the value defensively to `l1_gas_price_margin_percent` so that the maximum plausible price drift within `max_staleness_seconds` is provably bounded below the margin used in `validate_proposal.rs`. Consider making the staleness bound configurable per-feed (as was ultimately done for `HEARTBEAT_TIME` in the referenced fix) rather than a single shared constant for both legs.

### Proof of Concept
1. Two honest nodes independently call `read_feed` for the ETH/USD Chainlink feed at the same `block_timestamp`, per `crates/apollo_l1_gas_price/src/chainlink_oracle/feed_read.rs`.
2. Node A's batcher-backed view returns a round `updated_at` = `block_timestamp - 20000s` (~5.5h old); Node B's returns a round `updated_at` = `block_timestamp - 100s` (fresh). Both pass the `max_staleness_seconds = 90000` check.
3. If ETH/USD price moved >10% between those two rounds' timestamps (realistic during a volatile day), the two nodes derive `eth_to_fri_rate` values differing by >`l1_gas_price_margin_percent`.
4. Node A proposes a block with `l1_gas_price_fri` computed from its stale rate; Node B, acting as validator, rejects via the `within_margin` check in `validate_proposal.rs`, logging "L1 gas price mismatch."
5. If a quorum of validators independently sampled divergent rounds beyond the margin, the round fails to reach agreement, repeating until enough validators' local reads happen to converge — a liveness degradation directly caused by the oversized staleness window.

### Citations

**File:** crates/apollo_l1_gas_price_config/src/config.rs (L334-361)
```rust
impl Default for ChainlinkOracleConfig {
    fn default() -> Self {
        // Chainlink proxy addresses on Starknet mainnet. The proxies are used rather than the
        // aggregators behind them, because aggregators are rotated without notice.
        const ETH_USD_PROXY_ADDRESS: &str =
            "0x06b2ef9b416ad0f996b2a8ac0dd771b1788196f51c96f5b000df2e47ac756d26";
        const STRK_USD_PROXY_ADDRESS: &str =
            "0x076a0254cdadb59b86da3b5960bf8d73779cac88edc5ae587cab3cedf03226ec";
        // The feeds guarantee an update at least once per 24h heartbeat; the extra hour absorbs
        // the delay between the heartbeat deadline and the update landing on-chain.
        const HEARTBEAT_PLUS_MARGIN_SECONDS: u64 = (24 + 1) * 3600;
        // `updated_at` and the block timestamp it is checked against both come from a
        // sequencer's clock, so this only covers the skew between them.
        const MAX_FUTURE_UPDATED_AT_SECONDS: u64 = 300;

        Self {
            eth_usd_feed_address: parse_feed_address(ETH_USD_PROXY_ADDRESS),
            strk_usd_feed_address: parse_feed_address(STRK_USD_PROXY_ADDRESS),
            freshness: FreshnessWindow {
                max_staleness_seconds: HEARTBEAT_PLUS_MARGIN_SECONDS,
                max_future_updated_at_seconds: MAX_FUTURE_UPDATED_AT_SECONDS,
            },
            sampling_interval_seconds: 900, // 15 minutes
            // Successful reads are sampled once per sampling interval, so a failure that waited
            // for the next sample would freeze the price for that long.
            failure_retry_interval_seconds: 60,
        }
    }
```

**File:** crates/apollo_l1_gas_price/src/chainlink_oracle/feed_read.rs (L85-92)
```rust
    if block_timestamp.saturating_sub(round.updated_at) > feed.freshness.max_staleness_seconds {
        return Err(ExchangeRateOracleClientError::StaleFeedError {
            pair,
            updated_at: round.updated_at,
            block_timestamp,
            max_staleness_seconds: feed.freshness.max_staleness_seconds,
        });
    }
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L146-174)
```rust
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
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L315-330)
```rust
    Ok(starknet_api::block::BlockInfo {
        block_number: init.height,
        block_timestamp: BlockTimestamp(init.timestamp),
        sequencer_address: init.builder,
        gas_prices: GasPrices {
            strk_gas_prices: GasPriceVector {
                l1_gas_price: l1_gas_price_fri,
                l1_data_gas_price: l1_data_gas_price_fri,
                l2_gas_price: l2_gas_price_fri,
            },
            eth_gas_prices: GasPriceVector {
                l1_gas_price: l1_gas_price_wei,
                l1_data_gas_price: l1_data_gas_price_wei,
                l2_gas_price: l2_gas_price_wei,
            },
        },
```

**File:** crates/apollo_l1_gas_price/src/chainlink_oracle/mod.rs (L99-110)
```rust
/// Reads Chainlink's on-chain Starknet price feeds through the sequencer's own batcher.
///
/// Consensus calls `fetch_rate` on every proposal build and validate, so the call must not block on
/// the batcher: the feed is read by a background query spawned at most once per
/// `sampling_interval_seconds`, and every caller is served the last valid read while that read is
/// within `MAX_FALLBACK_SAMPLING_INTERVALS` of the caller's own block timestamp.
///
/// Reads are not deterministic across nodes: `call_contract` executes against the batcher's latest
/// committed block rather than state pinned to the queried timestamp, so two nodes can read
/// different rounds for the same block timestamp. Chainlink's deviation threshold is far inside the
/// `l1_gas_price_margin_percent` validators compare within, so this is not expected to reject
/// proposals.
```

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_1.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 4000000000,
    "max_block_size": 5000000000,
    "min_gas_price": "0x1dcd65000",
    "l1_gas_price_margin_percent": 10
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L325-351)
```rust
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
