### Title
`ExchangeRateOracleClient::resolve_query` Accepts Oracle Rates Without Min/Max Range Validation, Committing Wrong L1 Gas Prices in FRI to Every Block - (`crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs`)

---

### Summary

`resolve_query` in `exchange_rate_oracle.rs` validates only that the returned rate is non-zero and carries 18 decimals. It performs no min/max plausibility check on the rate value itself. If the external oracle has a circuit-breaker that clamps the ETH→STRK rate to a floor or ceiling during extreme market conditions (the exact mechanism described in the Chainlink/Venus report), the sequencer silently accepts the clamped rate, converts all L1 WEI gas prices to FRI using the wrong multiplier, and commits those wrong prices into every block until the oracle recovers. Because both the proposer and the validator call the same oracle client, they independently derive the same wrong FRI prices; the `within_margin` check in `is_proposal_init_valid` therefore passes, and the bad prices are never rejected.

---

### Finding Description

**Root cause — `resolve_query` (lines 188–212):**

```rust
// crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs
if rate == 0 {
    return Err(ExchangeRateOracleClientError::InvalidRateError(
        "rate must be non-zero".to_string(),
    ));
}
// ...
if decimals != EXCHANGE_RATE_DECIMALS {
    return Err(...);
}
// No min/max range check — any non-zero rate with 18 decimals is accepted.
Ok(rate)
```

The only guards are `rate != 0` and `decimals == 18`. A rate of `1` (10⁻¹⁸ of the expected ~10²¹) or `u128::MAX` passes both checks.

**Propagation path:**

1. `get_l1_prices_in_fri_and_wei_and_conversion_rate` (`utils.rs` lines 148–175) calls `get_rate(timestamp)` and `get_price_info(timestamp)` in parallel.
2. `apply_fee_transformations` (`utils.rs` lines 273–286) clamps the raw WEI prices to `[min_l1_gas_price_wei, max_l1_gas_price_wei]` — this protects the WEI values but does nothing about the rate.
3. `L1PricesInFri::convert_from_wei(&prices_in_wei, eth_to_fri_rate)` multiplies the (now-clamped) WEI price by the unchecked rate. If the rate is 10× too high, the FRI price is 10× too high; if 10× too low, 10× too low.
4. The resulting `(L1PricesInFri, L1PricesInWei, eth_to_fri_rate)` tuple is returned and embedded in `ProposalInit`.

**Why the validator does not catch it:**

`is_proposal_init_valid` (`validate_proposal.rs` lines 322–368) calls `get_l1_prices_in_fri_and_wei` with the same oracle client. Both proposer and validator independently query the same oracle endpoint and receive the same clamped rate. They therefore compute identical wrong FRI prices. The `within_margin` check compares the proposer's FRI price against the validator's own FRI price — both derived from the same wrong rate — so the check trivially passes.

**Why the fallback chain does not help:**

The fallback chain (`utils.rs` lines 184–222) activates only when the oracle returns an *error* (`Err`). A circuit-breaker returning a clamped-but-non-zero rate with 18 decimals returns `Ok(clamped_rate)`, bypassing the fallback entirely.

---

### Impact Explanation

Every block produced while the oracle is clamped carries wrong L1 gas prices in FRI. Concretely:

- **Rate too high** (oracle floor during ETH crash): FRI prices are inflated. Every transaction that pays L1 gas in STRK is overcharged. The sequencer collects excess fees; users lose funds.
- **Rate too low** (oracle ceiling during ETH pump): FRI prices are deflated. Every transaction is undercharged. The sequencer subsidises L1 costs; the protocol loses revenue and may accumulate bad debt on L1 settlement.

Both cases match the allowed Critical scope: *"Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact."*

The wrong prices are committed into `BlockInfo.gas_prices` (via `convert_to_sn_api_block_info`, `utils.rs` lines 301–348), which the blockifier uses for all fee calculations in that block. The error is therefore not isolated to a single transaction but affects every transaction in every block produced during the oracle anomaly.

---

### Likelihood Explanation

The ETH→STRK oracle is a custom HTTP endpoint (`ExchangeRateOracleConfig.url_header_list`). If that endpoint is backed by a price aggregator that itself has a circuit-breaker (a common design for production price feeds), the clamped value will be returned as a normal `200 OK` JSON response with a valid hex price and `"decimals": 18`. No error is signalled; the sequencer has no way to distinguish a clamped rate from a real one without a configured min/max range. The Venus/LUNA incident shows this is a realistic market scenario, not a theoretical one.

---

### Recommendation

Add configurable `min_rate` and `max_rate` fields to `ExchangeRateOracleConfig` and enforce them in `resolve_query`:

```rust
// After the existing zero-check:
if let (Some(min), Some(max)) = (config.min_rate, config.max_rate) {
    if rate < min || rate > max {
        return Err(ExchangeRateOracleClientError::InvalidRateError(
            format!("rate {rate} outside configured range [{min}, {max}]"),
        ));
    }
}
```

When `resolve_query` returns an error, the existing fallback chain in `get_l1_prices_in_fri_and_wei_and_conversion_rate` (previous-block prices → configured minimums) activates correctly, preserving liveness while preventing the wrong rate from being used.

---

### Proof of Concept

**Scenario**: ETH crashes 90 %. The oracle's circuit-breaker clamps the ETH→STRK rate at `max_rate = 10 × true_rate`.

1. `ExchangeRateOracleClient::fetch_rate` calls `spawn_query`, which calls `resolve_query`.
2. `resolve_query` receives `{"price": "0x<10x_rate_hex>", "decimals": 18}`. Rate is non-zero, decimals match → `Ok(10x_rate)` returned.
3. `get_l1_prices_in_fri_and_wei_and_conversion_rate` receives `Ok(10x_rate)`.
4. `apply_fee_transformations` clamps `base_fee_per_gas` to `max_l1_gas_price_wei` (say 100 Gwei).
5. `L1PricesInFri::convert_from_wei(100_Gwei_wei, 10x_rate)` → FRI price = 10× correct value.
6. Proposer embeds this in `ProposalInit.l1_gas_price_fri`.
7. Validator calls the same oracle, gets the same `10x_rate`, computes the same FRI price.
8. `within_margin(proposed_fri, reference_fri, 10%)` → `|10x - 10x| = 0 ≤ margin` → **passes**.
9. Block is accepted. Every transaction in the block is charged 10× the correct L1 gas fee in STRK. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs (L185-212)
```rust
    let rate = u128::from_str_radix(price.trim_start_matches("0x"), 16).map_err(|e| {
        ExchangeRateOracleClientError::ParseError(format!("Failed to parse price {price}: {e}"))
    })?;
    if rate == 0 {
        return Err(ExchangeRateOracleClientError::InvalidRateError(
            "rate must be non-zero".to_string(),
        ));
    }
    // Extract decimals from API response. Also returns MissingFieldError if value is not a number.
    let decimals = match json.get("decimals").and_then(|v| v.as_u64()) {
        Some(decimals) => decimals,
        None => {
            return Err(ExchangeRateOracleClientError::MissingFieldError(
                "decimals".to_string(),
                body,
            ));
        }
    };
    if decimals != EXCHANGE_RATE_DECIMALS {
        return Err(ExchangeRateOracleClientError::InvalidDecimalsError(
            EXCHANGE_RATE_DECIMALS,
            decimals,
        ));
    }
    metrics.success_count.increment(1);
    set_unix_now_seconds(metrics.last_success_timestamp);
    metrics.rate.set_lossy(rate);
    Ok(rate)
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L159-182)
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
        } else {
            warn!(
                "Failed to convert L1 gas prices to FRI: {:?}",
                l1_gas_prices_fri_result.clone().err()
            );
        }
    }
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L273-286)
```rust
pub(crate) fn apply_fee_transformations(
    price_info: &mut PriceInfo,
    gas_price_params: &GasPriceParams,
) {
    price_info.base_fee_per_gas = price_info
        .base_fee_per_gas
        .saturating_add(gas_price_params.l1_gas_tip_wei)
        .clamp(gas_price_params.min_l1_gas_price_wei, gas_price_params.max_l1_gas_price_wei);

    price_info.blob_fee = GasPrice(
        (gas_price_params.l1_data_gas_price_multiplier * price_info.blob_fee.0).to_integer(),
    )
    .clamp(gas_price_params.min_l1_data_gas_price_wei, gas_price_params.max_l1_data_gas_price_wei);
}
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L301-348)
```rust
pub(crate) fn convert_to_sn_api_block_info(
    init: &ProposalInit,
) -> Result<starknet_api::block::BlockInfo, StarknetApiError> {
    if init.l1_gas_price_fri.0 == 0
        || init.l1_gas_price_wei.0 == 0
        || init.l1_data_gas_price_fri.0 == 0
        || init.l1_data_gas_price_wei.0 == 0
        || init.l2_gas_price_fri.0 == 0
    {
        warn!("Zero gas price detected in block info: {:?}", init);
    }

    let l1_gas_price_fri = NonzeroGasPrice::new(init.l1_gas_price_fri)?;
    let l1_data_gas_price_fri = NonzeroGasPrice::new(init.l1_data_gas_price_fri)?;
    let l1_gas_price_wei = NonzeroGasPrice::new(init.l1_gas_price_wei)?;
    let l1_data_gas_price_wei = NonzeroGasPrice::new(init.l1_data_gas_price_wei)?;
    let l2_gas_price_fri = NonzeroGasPrice::new(init.l2_gas_price_fri)?;
    let proposal_init_info = PreviousProposalInitInfo::from(init);
    let eth_to_fri_rate = calculate_eth_to_fri_rate(&proposal_init_info)?;

    let l2_gas_price_wei = NonzeroGasPrice::new(init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?)
        .inspect_err(|_| {
            warn!(
                "L2 gas price in wei is zero! Conversion rate: {eth_to_fri_rate}, L2 gas price in \
                 FRI: {}",
                init.l2_gas_price_fri
            )
        })?;
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
        use_kzg_da: init.l1_da_mode.is_use_kzg_da(),
        starknet_version: init.starknet_version,
    })
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L322-368)
```rust
    let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
        l1_gas_price_provider,
        init_proposed.timestamp,
        proposal_init_validation.previous_proposal_init.as_ref(),
        gas_price_params,
    )
    .await;
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L421-438)
```rust
/// Returns whether `proposed` is within `margin_percent` of the locally-trusted `reference`,
/// i.e. within the symmetric band `[reference*(1-m), reference*(1+m)]`.
///
/// The band is anchored to `reference` (the node's own L1 oracle read), not to the
/// proposer-supplied `proposed`: anchoring to `proposed` would let a malicious proposer scale the
/// band width with its own input and widen it in its favor.
fn within_margin(proposed: GasPrice, reference: GasPrice, margin_percent: u128) -> bool {
    // For small numbers (e.g., less than 10 wei, if margin is 10%), even an off-by-one
    // error might be bigger than the margin, even if it is just a rounding error.
    // We make an exception for such mismatch, and don't bother checking percentages
    // if the difference in price is only one wei.
    if proposed.0.abs_diff(reference.0) <= GAS_PRICE_ABS_DIFF_MARGIN {
        return true;
    }
    // Saturate: `reference.0 * margin_percent` can overflow u128 on large WEI prices.
    let margin = reference.0.saturating_mul(margin_percent) / 100;
    proposed.0.abs_diff(reference.0) <= margin
}
```
