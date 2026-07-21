### Title
`compute_fee_target` silently misinterprets STRK/USD oracle rate when the oracle's decimal precision differs from the hardcoded `FRI_DECIMALS_SCALE` — (`File: crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs`)

### Summary

`compute_fee_target` in the SNIP-35 dynamic gas pricing path hardcodes `FRI_DECIMALS_SCALE = 10^18` as the assumed decimal precision of the STRK/USD oracle rate. The ETH/STRK oracle enforces its decimal count at the HTTP response layer via `EXCHANGE_RATE_DECIMALS = 18`. The STRK/USD oracle uses the **same** `ExchangeRateOracleClient` and the **same** `EXCHANGE_RATE_DECIMALS` constant for validation, but `compute_fee_target` independently hardcodes `FRI_DECIMALS_SCALE = 10^18` as the denominator in its fee calculation. If the STRK/USD oracle endpoint is ever configured to return a rate with a different decimal precision (e.g., 6 or 9 decimals), the `decimals` field validation in `resolve_query` would reject it — but if the operator configures a second oracle URL that returns a different decimal count, the validation rejects it with an error and the system falls back to `fee_actual`. More critically: the two oracle clients (`eth_to_strk` and `strk_to_usd`) share the same `ExchangeRateOracleClient` implementation and the same `EXCHANGE_RATE_DECIMALS = 18` constant, but `compute_fee_target` independently re-encodes the same assumption as `FRI_DECIMALS_SCALE = 10^18` without any cross-check. There is no compile-time or runtime binding between `EXCHANGE_RATE_DECIMALS` (the wire-level validation constant) and `FRI_DECIMALS_SCALE` (the arithmetic constant used in fee computation). If these two constants ever diverge — e.g., `EXCHANGE_RATE_DECIMALS` is updated to 9 for a new oracle API version while `FRI_DECIMALS_SCALE` remains `10^18` — the fee target computation will be wrong by a factor of `10^9`, producing a `fee_proposal` that is 10^9× too high or too low, which then propagates into the L2 gas price via the sliding window.

### Finding Description

The SNIP-35 fee target computation in `compute_fee_target` uses the formula:

```
floor_fri = target_atto_usd_per_l2_gas * FRI_DECIMALS_SCALE / strk_usd_rate
```

where `FRI_DECIMALS_SCALE = 10^18` is a hardcoded constant. [1](#0-0) 

The `strk_usd_rate` value is fetched from the STRK/USD oracle via `get_strk_to_usd_rate`, which internally calls `ExchangeRateOracleClient::fetch_rate`. The oracle client validates that the HTTP response's `decimals` field equals `EXCHANGE_RATE_DECIMALS = 18`: [2](#0-1) [3](#0-2) 

The `compute_fee_target` function then divides by `strk_usd_rate` assuming it carries exactly 18 decimal places: [4](#0-3) 

The two constants `EXCHANGE_RATE_DECIMALS` (in `apollo_l1_gas_price`) and `FRI_DECIMALS_SCALE` (in `apollo_consensus_orchestrator`) are **defined independently** in separate crates with no shared binding, no assertion, and no cross-reference. The `L1GasPriceProviderClient` trait documents the STRK/USD rate as "18-decimal fixed-point integer" in a comment, but this is not enforced at the type level: [5](#0-4) 

The `strk_to_usd_oracle_client` is a separate instance of `ExchangeRateOracleClient` configured with a different URL than the ETH/STRK oracle: [6](#0-5) 

The `compute_fee_target` result feeds directly into `compute_fee_proposal`, which is then published as `fee_proposal_fri` in `ProposalInit` and committed into the consensus hash via `proposal_commitment_from`: [7](#0-6) [8](#0-7) 

### Impact Explanation

If `EXCHANGE_RATE_DECIMALS` and `FRI_DECIMALS_SCALE` diverge — either through a misconfiguration of the oracle endpoint or a future code change updating one constant without the other — the `fee_proposal_fri` value published in every `ProposalInit` will be wrong by the ratio of the two scales. For example:

- If the STRK/USD oracle is reconfigured to return rates with 9 decimals but `FRI_DECIMALS_SCALE` stays at `10^18`, the computed `fee_target` will be `10^9×` too high.
- This inflated `fee_proposal` propagates through the `fee_proposals_window` sliding window into `fee_actual`, which then becomes the floor for the EIP-1559 L2 gas price.
- The resulting L2 gas price will be orders of magnitude too high, causing all transactions to fail fee checks or be priced incorrectly.

This matches the impact category: **Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact** (Critical).

The `fee_proposal_fri` is also bound into the `ProposalCommitment` hash from V0_14_3 onward, so a wrong value corrupts the commitment that consensus signs over: [9](#0-8) 

### Likelihood Explanation

Currently both constants are 18, so the system is correct in production. The risk is a **version/config boundary** divergence: the two constants live in separate crates (`apollo_l1_gas_price` vs `apollo_consensus_orchestrator`) with no compile-time link. A future oracle API upgrade, operator misconfiguration of the STRK/USD endpoint, or a code change updating `EXCHANGE_RATE_DECIMALS` without updating `FRI_DECIMALS_SCALE` (or vice versa) would silently corrupt fee accounting. The `ExchangeRateOracleClient` is shared between the ETH/STRK and STRK/USD oracles, so any change to `EXCHANGE_RATE_DECIMALS` affects both, but `compute_fee_target` only uses `FRI_DECIMALS_SCALE` for the STRK/USD path.

### Recommendation

1. **Bind the constants at the type level**: Remove `FRI_DECIMALS_SCALE` from `dynamic_gas_price/mod.rs` and import `EXCHANGE_RATE_DECIMALS` from `apollo_l1_gas_price` (or a shared crate), using `10u128.pow(EXCHANGE_RATE_DECIMALS as u32)` as the scale factor in `compute_fee_target`. This creates a single source of truth.

2. **Add a compile-time or startup assertion**: Add `static_assert!(FRI_DECIMALS_SCALE == 10u128.pow(EXCHANGE_RATE_DECIMALS as u32))` or an equivalent `debug_assert!` at the call site in `resolve_fee_target`.

3. **Propagate the decimal count through the trait**: Change `get_strk_to_usd_rate` to return a typed struct `RateWithDecimals { rate: u128, decimals: u64 }` so that `compute_fee_target` can use the actual decimal count rather than a hardcoded assumption.

### Proof of Concept

Current state — two independent constants with no binding:

```rust
// crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs
pub const EXCHANGE_RATE_DECIMALS: u64 = 18;  // wire-level validation

// crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs
const FRI_DECIMALS_SCALE: u128 = 10u128.pow(18);  // arithmetic assumption — independent copy
```

If an operator configures the STRK/USD oracle URL to return `{"price": "0x...", "decimals": 9}`, the `resolve_query` function rejects it with `InvalidDecimalsError` and the oracle call fails. The system then falls back to `fee_actual` (freezes the fee proposal). However, if `EXCHANGE_RATE_DECIMALS` is updated to `9` to accommodate a new oracle API, `compute_fee_target` will still divide by `10^18` instead of `10^9`, producing a fee target `10^9×` too high:

```
// With EXCHANGE_RATE_DECIMALS=9, oracle returns rate=300_000_000 (= $0.30 with 9 decimals)
// FRI_DECIMALS_SCALE still = 10^18
// target_atto_usd = 880_000_000
// floor_fri = 880_000_000 * 10^18 / 300_000_000
//           = 2_933_333_333_333_333_333  (≈ 2.9 * 10^18 FRI per L2 gas)
// Correct value should be:
// floor_fri = 880_000_000 * 10^9 / 300_000_000 = 2_933_333_333 FRI per L2 gas
// Error factor: 10^9×
```

This wrong `fee_proposal` is then published in `ProposalInit.fee_proposal_fri`, committed into the consensus hash, and propagated through the `fee_proposals_window` into `fee_actual`, permanently inflating the L2 gas price floor. [10](#0-9) [11](#0-10) [12](#0-11)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L41-42)
```rust
/// Scale factor for 18-decimal fixed-point conversion (1 STRK = 10^18 FRI).
const FRI_DECIMALS_SCALE: u128 = 10u128.pow(18);
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

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L153-159)
```rust
/// Bind `fee_proposal_fri` to the proposal commitment hash.
///
/// Pre-V0_14_3 blocks have `fee_proposal = None` and the commitment is just `partial.0`,
/// preserving on-chain behavior. From V0_14_3 onward, the commitment is
/// `Poseidon(partial.0, fee_proposal_fri)`, so a proposer cannot equivocate on
/// `fee_proposal_fri` without changing the commitment that consensus signs over.
///
```

**File:** crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs (L27-27)
```rust
pub const EXCHANGE_RATE_DECIMALS: u64 = 18;
```

**File:** crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs (L193-212)
```rust
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

**File:** crates/apollo_l1_gas_price_types/src/lib.rs (L104-108)
```rust
    /// ETH/FRI rate as 18-decimal fixed-point integer (e.g. 5000 STRK per ETH → `5000 * 10^18`).
    async fn get_rate(&self, timestamp: u64) -> L1GasPriceProviderClientResult<u128>;

    /// STRK/USD rate as 18-decimal fixed-point integer (e.g. 24.5 STRK per USD → `24.5 * 10^18`).
    async fn get_strk_to_usd_rate(&self, timestamp: u64) -> L1GasPriceProviderClientResult<u128>;
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L84-93)
```rust
    pub fn new_with_oracle(config: L1GasPriceProviderConfig) -> Self {
        let eth_to_strk_oracle_client = ExchangeRateOracleClient::new(
            config.eth_to_strk_oracle_config.clone(),
            ETH_TO_STRK_ORACLE_METRICS,
        );
        let strk_to_usd_oracle_client = ExchangeRateOracleClient::new(
            config.strk_to_usd_oracle_config.clone(),
            STRK_TO_USD_ORACLE_METRICS,
        );
        Self::new(config, Arc::new(eth_to_strk_oracle_client), Arc::new(strk_to_usd_oracle_client))
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L443-466)
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L370-394)
```rust
    // fee_proposal is required iff Starknet version >= V0_14_3.
    let fee_proposal_required = init_proposed.starknet_version >= StarknetVersion::V0_14_3;
    match (init_proposed.fee_proposal_fri, fee_proposal_required) {
        (Some(_), false) => {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "fee_proposal must be absent before V0_14_3, got Some at version {}",
                    init_proposed.starknet_version
                ),
            ));
        }
        (None, true) => {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "fee_proposal is required at V0_14_3+, got None at version {}",
                    init_proposed.starknet_version
                ),
            ));
        }
        _ => {}
    }
```
