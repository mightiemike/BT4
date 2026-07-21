### Title
Gateway L2 Gas Price Threshold Truncated by Integer Division Admits Below-Minimum Transactions - (File: `crates/apollo_gateway/src/stateful_transaction_validator.rs`)

### Summary
`validate_tx_l2_gas_price_within_threshold` computes the minimum acceptable L2 gas price by multiplying `previous_block_l2_gas_price` by `min_gas_price_percentage/100` and then calling `.to_integer()`, which truncates (floors) the result. Because the threshold is rounded down, the gateway admits transactions whose L2 gas price is strictly below the operator-intended minimum.

### Finding Description
In `crates/apollo_gateway/src/stateful_transaction_validator.rs`, the threshold is computed as:

```rust
let gas_price_threshold_multiplier =
    Ratio::new(self.config.min_gas_price_percentage.into(), 100_u128);
let threshold = (gas_price_threshold_multiplier
    * previous_block_l2_gas_price.get().0)
    .to_integer();
if tx_l2_gas_price.0 < threshold {
    return Err(...)
}
```

`Ratio::to_integer()` truncates toward zero (floor for positive values), so:

```
threshold = floor(min_gas_price_percentage × previous_block_l2_gas_price / 100)
```

The admission predicate is `tx_l2_gas_price >= threshold`. Because `threshold` is floored, the effective lower bound is up to `(100 − 1)/100 = 0.99` FRI units below the exact intended minimum. A transaction whose gas price equals `floor(...)` passes even when the exact ratio is non-integer and the price is below the true threshold.

The existing unit tests in `crates/apollo_gateway/src/stateful_transaction_validator_test.rs` only exercise `previous_block_l2_gas_price = 100`, which is always divisible by 100 for any `min_gas_price_percentage`, so the truncation error is never triggered by the test suite. [1](#0-0) [2](#0-1) 

### Impact Explanation
The gateway's stateful validator is the admission gate before the mempool. A transaction that passes this check is forwarded to the mempool and eventually sequenced. By setting `tx_l2_gas_price` to exactly `floor(min_gas_price_percentage × previous_block_l2_gas_price / 100)`, an unprivileged user can submit a transaction whose L2 gas price is below the operator-configured minimum, bypassing the intended spam/congestion filter. This matches the impact: **High — Mempool/gateway admission accepts invalid transactions before sequencing**. [3](#0-2) 

### Likelihood Explanation
The precision loss occurs whenever `min_gas_price_percentage × previous_block_l2_gas_price` is not divisible by 100. With `min_gas_price_percentage` as a `u8` (default 100, but configurable to e.g. 80 or 50) and `previous_block_l2_gas_price` being an arbitrary `u128`, non-divisibility is the common case. Any user who inspects the previous block's L2 gas price can trivially compute the floored threshold and submit a transaction at exactly that value. [4](#0-3) 

### Recommendation
Avoid dividing before comparing. Multiply both sides of the inequality to eliminate the division entirely:

```rust
// Before (truncates threshold downward):
let threshold = (gas_price_threshold_multiplier * previous_block_l2_gas_price.get().0)
    .to_integer();
if tx_l2_gas_price.0 < threshold { ... }

// After (exact, no precision loss):
let percentage = u128::from(self.config.min_gas_price_percentage);
if tx_l2_gas_price.0.saturating_mul(100) < percentage.saturating_mul(previous_block_l2_gas_price.get().0) {
    return Err(...);
}
```

Alternatively, replace `.to_integer()` with `.ceil().to_integer()` so the threshold rounds up rather than down. [5](#0-4) 

### Proof of Concept
Configuration: `min_gas_price_percentage = 50`, `previous_block_l2_gas_price = 101`.

- Exact threshold: `50 × 101 / 100 = 50.5`
- Computed threshold (floored): `50`
- A transaction with `tx_l2_gas_price = 50` satisfies `50 < 50 → false`, so it **passes** the check.
- The intended threshold is `50.5`, so the transaction should be **rejected**.

The attacker reads the previous block's L2 gas price (public on-chain data), computes `floor(min_gas_price_percentage × price / 100)`, and submits a transaction at that exact price. The gateway admits it; the mempool queues it; it is sequenced. The maximum under-pricing is bounded by `< 1 FRI unit` per transaction, but the structural bypass of the admission filter is unconditional whenever the product is non-divisible by 100. [6](#0-5)

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L359-390)
```rust
    fn validate_tx_l2_gas_price_within_threshold(
        &self,
        tx_resource_bounds: ValidResourceBounds,
        previous_block_l2_gas_price: NonzeroGasPrice,
    ) -> StatefulTransactionValidatorResult<()> {
        match tx_resource_bounds {
            ValidResourceBounds::AllResources(tx_resource_bounds) => {
                let tx_l2_gas_price = tx_resource_bounds.l2_gas.max_price_per_unit;
                let gas_price_threshold_multiplier =
                    Ratio::new(self.config.min_gas_price_percentage.into(), 100_u128);
                let threshold = (gas_price_threshold_multiplier
                    * previous_block_l2_gas_price.get().0)
                    .to_integer();
                if tx_l2_gas_price.0 < threshold {
                    return Err(StarknetError {
                        // We didn't have this kind of an error.
                        code: StarknetErrorCode::UnknownErrorCode(
                            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
                        ),
                        message: format!(
                            "Transaction L2 gas price {tx_l2_gas_price} is below the required \
                             threshold {threshold}.",
                        ),
                    });
                }
            }
            ValidResourceBounds::L1Gas(_) => {
                // No validation required for legacy transactions.
            }
        }
        Ok(())
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator_test.rs (L229-270)
```rust
#[rstest]
#[case::tx_gas_price_meets_threshold_exactly_pass(
    100_u128.try_into().unwrap(),
    100,
    100_u128.into(),
    Ok(())
)]
#[case::tx_gas_price_below_threshold_fail(
    100_u128.try_into().unwrap(),
    100,
    99_u128.into(),
    Err(StarknetError {
        code: StarknetErrorCode::UnknownErrorCode(
            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
        ),
        message: "Transaction L2 gas price 99 is below the required threshold 100.".to_string(),
    })
)]
#[case::tx_gas_price_meets_threshold_with_factor_pass(
    100_u128.try_into().unwrap(),
    50,
    50_u128.into(),
    Ok(())
)]
#[case::tx_gas_price_above_threshold_with_factor_pass(
    100_u128.try_into().unwrap(),
    50,
    51_u128.into(),
    Ok(())
)]
#[case::tx_gas_price_below_threshold_with_factor_fail(
    100_u128.try_into().unwrap(),
    50,
    49_u128.into(),
    Err(StarknetError {
        code: StarknetErrorCode::UnknownErrorCode(
            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
        ),
        message: "Transaction L2 gas price 49 is below the required threshold 50.".to_string(),
    })
)]
#[case::gas_price_check_disabled_when_percentage_zero_pass(
```

**File:** crates/apollo_gateway_config/src/config.rs (L285-286)
```rust
    // Minimum gas price as percentage of threshold to accept transactions.
    pub min_gas_price_percentage: u8, // E.g., 80 to require 80% of threshold.
```

**File:** crates/apollo_gateway_config/src/config.rs (L289-299)
```rust
impl Default for StatefulTransactionValidatorConfig {
    fn default() -> Self {
        StatefulTransactionValidatorConfig {
            validate_resource_bounds: true,
            max_allowed_nonce_gap: 200,
            reject_future_declare_txs: true,
            max_nonce_for_validation_skip: Nonce(Felt::ONE),
            min_gas_price_percentage: 100,
            versioned_constants_overrides: None,
        }
    }
```
