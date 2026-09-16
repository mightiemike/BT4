### Title
DoS on transaction confirmation due to zero-tolerance L2 gas price threshold computed from a stale previous-block reference - (File: crates/apollo_gateway/src/stateful_transaction_validator.rs)

### Summary
The gateway's stateful L2 gas price admission check (`validate_tx_l2_gas_price_within_threshold`) compares an incoming transaction's `max_price_per_unit` against a stale reference (the *previous* confirmed block's L2 gas price) using a default `min_gas_price_percentage` of 100%, i.e., zero tolerance. Because the L2 gas price is dynamically recomputed for every new block (via the EIP-1559-style fee market and the SNIP-35 oracle-driven adjustment), a transaction that exactly satisfies this admission threshold can still fail the independent, execution-time fee-bound check (`check_fee_bounds` in blockifier) the moment the actual price for the block being built rises above the stale reference used by the gateway. This is directly analogous to the reported DeFi bug: an overly tight, zero-margin comparison against a reference price that is known (via the code's own TODO) to be inexact causes legitimate transactions to be rejected/stuck.

### Finding Description
`StatefulTransactionValidator::validate_resource_bounds` fetches `previous_block_l2_gas_price` from the last committed block and passes it to `validate_tx_l2_gas_price_within_threshold`, with an explicit acknowledgment that this is an approximation: [1](#0-0) 

The threshold check enforces `tx_l2_gas_price >= min_gas_price_percentage% * previous_block_l2_gas_price`: [2](#0-1) 

`min_gas_price_percentage` defaults to `100`, i.e., zero slack above the (stale) previous-block price is required or tolerated: [3](#0-2) 

Separately, the actual block-building/execution path performs its own, independent gas-price sufficiency check against the *real* gas price of the block currently being built, raising `ResourceBoundsError::MaxGasPriceTooLow` if `max_price_per_unit` is below it: [4](#0-3) 

The L2 gas price is not static between blocks — it moves each block via the fee market (up to a bounded percentage under congestion, or gradually toward a configured minimum), and can jump meaningfully across a handful of blocks: [5](#0-4) 

Because a `min_gas_price_percentage` of 100 provides no buffer, a transaction admitted to the mempool with `max_price_per_unit` set exactly to the last confirmed block's price will fail the execution-time `check_fee_bounds` as soon as the price for the block actually being built increases (which can legitimately happen every block under sustained demand). This mirrors the reported pattern: an unforgivingly tight tolerance (analogous to `MAX_ADD_LP_SLIPPAGE_BPS = 33`) causes normal, expected price movement to trigger rejection.

### Impact Explanation
Transactions constructed with the minimally-accepted `max_price_per_unit` (which the gateway itself deems sufficient at admission) can be rejected during block building/re-execution once the network's L2 gas price increases even slightly, since the admission-time reference price is stale relative to the price actually enforced at inclusion time. This causes user transactions to be dropped/fail repeatedly, preventing their confirmation without manual resubmission with added margin — a availability/DoS impact on the affected sender's ability to get transactions included, consistent with Medium impact per the reference report's classification.

### Likelihood Explanation
This requires no privileged access: any unprivileged transaction sender who sets `max_price_per_unit` at or near the gateway-accepted floor (the intuitive/expected way to interact with a "minimum gas price percentage" requirement) will be affected whenever the network experiences even modest congestion between the time the reference price is read and the time the transaction's target block is built. Given the fee market updates every block and can move price for several consecutive blocks under sustained load, this scenario is expected to occur under moderate network activity, matching Medium likelihood.

### Recommendation
Align the gateway's admission-time gas price reference with the price that will actually be enforced at execution (e.g., use the projected/next-block L2 gas price as noted in the existing `TODO(Arni)` comment), and/or set a `min_gas_price_percentage` default with headroom (e.g., requiring some margin below or above 100%, consistent with expected per-block price volatility) so that transactions admitted by the gateway remain valid through inclusion under normal price fluctuation.

### Proof of Concept
1. Gateway admits an invoke transaction with `resource_bounds.l2_gas.max_price_per_unit` set exactly equal to `previous_block_l2_gas_price` (satisfies `min_gas_price_percentage = 100` exactly), per `validate_tx_l2_gas_price_within_threshold`.
2. Before the transaction is included, the network experiences a congested block, causing `calculate_next_base_gas_price` to raise the L2 gas price for the next block (bounded increase per block, but nonzero).
3. When the sequencer attempts to include the transaction in that new block, `check_fee_bounds` compares `max_price_per_unit` against the new (higher) block gas price and raises `TransactionFeeError::InsufficientResourceBounds` / `ResourceBoundsError::MaxGasPriceTooLow`, rejecting a transaction that the gateway had accepted as sufficient.

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L228-240)
```rust
        if self.config.validate_resource_bounds {
            // TODO(Arni): getnext_l2_gas_price from the block header.
            let previous_block_l2_gas_price = self
                .gateway_fixed_block_state_reader
                .get_block_info()
                .await?
                .gas_prices
                .strk_gas_prices
                .l2_gas_price;
            self.validate_tx_l2_gas_price_within_threshold(
                executable_tx.resource_bounds(),
                previous_block_l2_gas_price,
            )?;
```

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

**File:** crates/apollo_gateway_config/src/config.rs (L285-299)
```rust
    // Minimum gas price as percentage of threshold to accept transactions.
    pub min_gas_price_percentage: u8, // E.g., 80 to require 80% of threshold.
}

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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L441-458)
```rust
                            if resource_bounds.max_price_per_unit < actual_gas_price.get() {
                                insufficiencies_resource.push(
                                    ResourceBoundsError::MaxGasPriceTooLow {
                                        resource: *resource,
                                        max_gas_price: resource_bounds.max_price_per_unit,
                                        actual_gas_price: (*actual_gas_price).into(),
                                    },
                                );
                            }
                            insufficiencies_resource
                        },
                    )
                    .collect::<Vec<_>>();
                if !insufficiencies.is_empty() {
                    return Err(Box::new(TransactionFeeError::InsufficientResourceBounds {
                        errors: insufficiencies,
                    }))?;
                }
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L173-227)
```rust
pub fn calculate_next_base_gas_price(
    price: GasPrice,
    gas_used: GasAmount,
    gas_target: GasAmount,
    min_gas_price: GasPrice,
) -> GasPrice {
    let versioned_constants = VersionedConstants::latest_constants();
    assert!(
        gas_target < versioned_constants.max_block_size,
        "Gas target must be lower than max block size."
    );
    assert!(gas_target.0 > 0, "Gas target must be greater than zero.");
    assert!(
        versioned_constants.gas_price_max_change_denominator > 0,
        "Denominator constant must be greater than zero."
    );

    // If the current price is below the minimum, apply a gradual adjustment and return early.
    // This allows the price to increase by at most 1/MIN_GAS_PRICE_INCREASE_DENOMINATOR per block.
    if price < min_gas_price {
        let max_increase = price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR;
        let adjusted = price.0 + max_increase;
        // Cap at min_gas_price to avoid overshooting
        let adjusted_price = adjusted.min(min_gas_price.0);
        info!(
            "Fee Market: Price {} below minimum gas price {}, adjusted price: {} )",
            price.0, min_gas_price.0, adjusted_price
        );
        return GasPrice(adjusted_price);
    }

    // Use U256 to avoid overflow, as multiplying a u128 by a u64 remains within U256 bounds.
    let gas_delta = U256::from(gas_used.0.abs_diff(gas_target.0));
    let gas_target_u256 = U256::from(gas_target.0);
    let price_u256 = U256::from(price.0);

    // Calculate price change by multiplying first, then dividing. This avoids the precision loss
    // that occurs when dividing before multiplying.
    let denominator =
        gas_target_u256 * U256::from(versioned_constants.gas_price_max_change_denominator);
    let price_change = (price_u256 * gas_delta) / denominator;

    let adjusted_price_u256 =
        if gas_used > gas_target { price_u256 + price_change } else { price_u256 - price_change };

    // Sanity check: ensure direction of change is correct
    assert!(
        gas_used > gas_target && adjusted_price_u256 >= price_u256
            || gas_used <= gas_target && adjusted_price_u256 <= price_u256
    );

    // Price should not realistically exceed u128::MAX, bound to avoid theoretical overflow.
    let adjusted_price = u128::try_from(adjusted_price_u256).unwrap_or(u128::MAX);
    GasPrice(max(adjusted_price, min_gas_price.0))
}
```
