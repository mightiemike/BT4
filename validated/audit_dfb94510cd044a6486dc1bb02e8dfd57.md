## Analog Found

### Title
Missing min/max cross-validation on `ContextDynamicConfig` gas-price bounds causes an unconditional panic in `apply_fee_transformations`, halting block production - (File: `crates/apollo_consensus_orchestrator_config/src/config.rs`)

### Summary
`ContextDynamicConfig` accepts independently-set `min_l1_gas_price_wei`/`max_l1_gas_price_wei` and `min_l1_data_gas_price_wei`/`max_l1_data_gas_price_wei` fields with no schema check that `min <= max`, unlike the sibling `AllRateBoundsConfig` in `apollo_l1_gas_price_config`, which explicitly rejects inverted bounds. These unvalidated bounds are later passed straight into `Ord::clamp`, which panics in Rust whenever `min > max`.

### Finding Description
`ContextDynamicConfig` is validated via `#[validate(schema(function = "validate_dynamic_config"))]`, but `validate_dynamic_config` only checks ordering/pricing of `min_l2_gas_price_per_height` entries; it never cross-checks `min_l1_gas_price_wei` against `max_l1_gas_price_wei`, nor `min_l1_data_gas_price_wei` against `max_l1_data_gas_price_wei`: [1](#0-0) 

This is the same class of bug as the reported `GPToke` issue: values that must satisfy `min <= max` are accepted as independent, unrelated config fields with no cross-field constructor/schema check. Notably, the codebase already recognizes this exact bug class and fixes it elsewhere — `AllRateBoundsConfig::validate_all_rate_bounds_config` explicitly rejects `minimum_micro_units >= maximum_micro_units`: [2](#0-1) 

But the equivalent guard is absent for `ContextDynamicConfig`'s L1 gas price bounds: [3](#0-2) 

These bounds are wrapped into `GasPriceParams` and consumed by `apply_fee_transformations`, which calls `.clamp(min, max)` directly: [4](#0-3) 

Rust's standard `Ord::clamp` (used here since `GasPrice` derives ordering) panics if `min > max`. Since `min_l1_gas_price_wei`/`max_l1_gas_price_wei` (and the data-gas equivalents) can be configured in either order without rejection, a misconfiguration (e.g., an operator or deployment template swapping the two values, or independently tuning them without realizing the invariant) leads straight to a panic.

### Impact Explanation
`apply_fee_transformations` runs on the "live data" happy path of `get_l1_prices_in_fri_and_wei_and_conversion_rate`, which is invoked on every block proposal both when building and when validating: [5](#0-4) 

Because `ContextDynamicConfig` is the shared dynamic config loaded by every node that runs the orchestrator, an inverted `min`/`max` bound is not a per-node quirk — every honest node loading the same misconfigured dynamic config panics identically on every proposal it builds or validates whenever the "live data" path is taken. This is a liveness failure: the network becomes unable to confirm new transactions (the specific impact accepted by the validation criteria) until the config is corrected and nodes restarted.

### Likelihood Explanation
The gap is a straightforward config-schema oversight: `validate_dynamic_config` was clearly written to guard `min_l2_gas_price_per_height` but not the four `min_l1_*_wei`/`max_l1_*_wei` fields, even though the sibling `AllRateBoundsConfig` in the L1-gas-price crate demonstrates the project's own established pattern for this exact class of check. No `#[validate(range)]` or nested schema function protects these fields, so any deployment/config change (human error, templating bug, or environment override) that sets `min_l1_gas_price_wei > max_l1_gas_price_wei` (or the data-gas pair) passes config loading successfully and only fails at runtime with a panic.

### Recommendation
Extend `validate_dynamic_config` in `crates/apollo_consensus_orchestrator_config/src/config.rs` to reject configs where `min_l1_gas_price_wei >= max_l1_gas_price_wei` or `min_l1_data_gas_price_wei >= max_l1_data_gas_price_wei`, mirroring the pattern already used in `validate_all_rate_bounds_config` in `crates/apollo_l1_gas_price_config/src/config.rs`. As defense-in-depth, `apply_fee_transformations` in `crates/apollo_consensus_orchestrator/src/utils.rs` could also use a saturating/checked clamp instead of `Ord::clamp` so that a bad config degrades rather than panics.

### Proof of Concept
1. Deploy/load a `ContextDynamicConfig` (e.g., via the node's TOML/JSON config or `apollo_deployments/resources/app_configs/consensus_manager_config.json`) with `min_l1_gas_price_wei` set above `max_l1_gas_price_wei` (or `min_l1_data_gas_price_wei` above `max_l1_data_gas_price_wei`).
2. `ContextDynamicConfig::validate()` succeeds because `validate_dynamic_config` never checks this relationship (only `min_l2_gas_price_per_height` is checked): [1](#0-0) 
3. On the node's first proposal build/validation with a live L1 gas price/oracle reading, `apply_fee_transformations` calls `.clamp(min_l1_gas_price_wei, max_l1_gas_price_wei)`: [6](#0-5) 
4. `Ord::clamp` panics because `min > max`, crashing the orchestrator task for that node; since every honest node runs the same dynamic config, all nodes panic identically, halting block production network-wide.

### Citations

**File:** crates/apollo_consensus_orchestrator_config/src/config.rs (L275-282)
```rust
    /// The minimum L1 gas price in wei.
    pub min_l1_gas_price_wei: u128,
    /// The maximum L1 gas price in wei.
    pub max_l1_gas_price_wei: u128,
    /// The minimum L1 data gas price in wei.
    pub min_l1_data_gas_price_wei: u128,
    /// The maximum L1 data gas price in wei.
    pub max_l1_data_gas_price_wei: u128,
```

**File:** crates/apollo_consensus_orchestrator_config/src/config.rs (L463-484)
```rust
fn validate_dynamic_config(
    config: &ContextDynamicConfig,
) -> Result<(), validator::ValidationError> {
    // Check that heights are in strictly ascending order using windows
    if !config.min_l2_gas_price_per_height.windows(2).all(|w| w[0].height < w[1].height) {
        return Err(validator::ValidationError::new(
            "min_l2_gas_price_per_height heights must be in strictly ascending order",
        ));
    }

    // Check that all prices are above the minimum
    for entry in &config.min_l2_gas_price_per_height {
        if entry.price < MIN_ALLOWED_GAS_PRICE {
            return Err(validator::ValidationError::new(
                "all prices in min_l2_gas_price_per_height must be at least 8 gwei (8000000000 \
                 fri)",
            ));
        }
    }

    Ok(())
}
```

**File:** crates/apollo_l1_gas_price_config/src/config.rs (L227-253)
```rust
fn validate_all_rate_bounds_config(config: &AllRateBoundsConfig) -> Result<(), ValidationError> {
    for (pair_name, bounds) in [
        ("eth_usd", &config.eth_usd),
        ("strk_usd", &config.strk_usd),
        ("eth_strk", &config.eth_strk),
    ] {
        if bounds.minimum_micro_units == 0 {
            return Err(create_validation_error(
                format!("{pair_name}.minimum_micro_units is zero"),
                "zero sanity bound",
                "A zero minimum disables the lower sanity bound; set it to the lowest plausible \
                 value.",
            ));
        }
        if bounds.minimum_micro_units >= bounds.maximum_micro_units {
            return Err(create_validation_error(
                format!(
                    "{pair_name}.minimum_micro_units ({}) is not below \
                     {pair_name}.maximum_micro_units ({})",
                    bounds.minimum_micro_units, bounds.maximum_micro_units
                ),
                "inverted sanity bounds",
                "Ensure each minimum sanity bound is strictly below its maximum.",
            ));
        }
    }
    Ok(())
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L140-164)
```rust
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
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L272-285)
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
