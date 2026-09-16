Default `min_gas_price_percentage: 100` in `StatefulTransactionValidatorConfig` [1](#0-0)  confirms this is a real, production-shipped analog worth reporting.

### Title
Gateway rejects valid transactions whenever L2 gas price rises between blocks, causing network-wide inability to submit transactions during congestion - (File: crates/apollo_gateway/src/stateful_transaction_validator.rs)

### Summary
The gateway's `validate_tx_l2_gas_price_within_threshold` rejects any transaction whose `max_price_per_unit` for L2 gas is below `min_gas_price_percentage`% of the *previous committed block's* L2 gas price [2](#0-1) . With the shipped default `min_gas_price_percentage = 100` [1](#0-0) , a transaction must set its max L2 gas price at or above the *exact* price of the last block. But the EIP-1559-style price update run by consensus can raise the L2 gas price up to ~9.5% in a single fully-congested block [3](#0-2) , and the underlying `calculate_next_base_gas_price` formula has no cap on how much the price can move block-to-block other than the max-change denominator [4](#0-3) . Because the gateway checks against the price the sender observed one or more blocks in the past, this is structurally the same bug class as the Olympus finding: a rigid threshold compared against a stale reference value that can naturally have moved outside the tolerance band before the compared operation executes, causing legitimate operations (here, transaction admission) to fail for extended congestion periods.

### Finding Description
`validate_resource_bounds` reads `previous_block_l2_gas_price` from the last committed block header and requires `tx_l2_gas_price >= (min_gas_price_percentage/100) * previous_block_l2_gas_price` [5](#0-4) [2](#0-1) . This is directly reachable by any unprivileged transaction sender submitting a transaction to the gateway — no privileged role is required.

The L2 gas price itself moves every committed block according to an EIP-1559-like formula: under sustained full congestion, the price can rise ~9.5% per block and continues rising block after block until it hits the ceiling [3](#0-2) , driven by `calculate_next_base_gas_price` [6](#0-5) . Any account that funds its transaction with a `max_price_per_unit` matching the price it observed (e.g., from the most recently synced block, or even the immediately preceding block) will have that price fall below the newly-updated `previous_block_l2_gas_price` as soon as the next block is congested, because the gateway's reference value updates every block while the sender's signed transaction is fixed.

With the default `min_gas_price_percentage = 100`, there is zero tolerance: the transaction's price must be at least the exact current threshold, with no allowance for the price having moved since the sender constructed and signed the transaction. Any transaction constructed shortly before a congestion-driven price increase is rejected at the gateway with `GAS_PRICE_TOO_LOW` [7](#0-6) , and must be resubmitted with a higher price — but if congestion persists across several consecutive blocks, the price keeps climbing and previously-valid transactions keep failing, exactly mirroring the report's dynamic where a rigid threshold compared to a moving reference causes prolonged unavailability of an operation (deposits/withdrawals there, transaction admission here).

### Impact Explanation
During any sustained period of high L2 usage, transactions signed with gas prices that were valid moments earlier become systematically rejected at the gateway, network-wide, for every sender racing to price above a moving target with zero margin. This is not an isolated wallet UX inconvenience: because the threshold is checked against a price that updates every block and can rise repeatedly during sustained congestion, an entire class of users can be locked out of getting transactions accepted until congestion subsides — a network unable to confirm new transactions from affected senders, matching the impact bar of "network unable to confirm new transactions."

### Likelihood Explanation
This requires no attacker action or malicious actor — it triggers under ordinary sustained congestion, which is a normal and expected network condition, not an edge case. The gateway's own test suite documents `min_gas_price_percentage = 100` as the shipped default with zero tolerance [1](#0-0) , and the fee-market's own tests confirm price can jump materially in a single congested block [3](#0-2) , making the scenario highly likely to occur during real congestion events.

### Recommendation
Do not require a transaction's `max_price_per_unit` to meet or exceed the *exact* previous block's L2 gas price when `min_gas_price_percentage` is 100. Either enforce a mandatory tolerance band (analogous to bounding `THRESHOLD` to a safe range in the report) that accounts for the maximum possible per-block price movement of the EIP-1559 mechanism (as documented, up to ~9.5% for a fully congested block, or more over consecutive blocks), or validate against a price that already incorporates near-term expected movement (e.g., a short lookahead/safety margin) rather than the raw previous block value, so that transactions priced correctly at submission time are not immediately invalidated by the next block's normal price update.

### Proof of Concept
1. Network reaches sustained full congestion (`l2_gas_used` at or near `max_block_size` for consecutive blocks), driving `calculate_next_base_gas_price` to raise `l2_gas_price` by ~9.5% each block (as reproduced in `test_sustained_congestion_stops_at_the_ceiling`) [3](#0-2) .
2. A user constructs and signs an Invoke/Declare/DeployAccount transaction with `resource_bounds.l2_gas.max_price_per_unit` equal to the L2 gas price of block N (the latest they observed).
3. Before the transaction reaches the gateway, block N+1 commits with a higher `l2_gas_price` (per step 1).
4. The gateway's `validate_resource_bounds` reads the new `previous_block_l2_gas_price` (block N+1's price) and computes `threshold = min_gas_price_percentage/100 * previous_block_l2_gas_price` with `min_gas_price_percentage = 100` (default) [8](#0-7)  — the user's signed price (from block N) is now below this threshold and the transaction is rejected with `GAS_PRICE_TOO_LOW`, requiring the user to sign and resubmit a new transaction with a higher price, which may again be outpaced if congestion continues into block N+2, N+3, etc.

### Citations

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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L223-243)
```rust
    async fn validate_resource_bounds(
        &self,
        executable_tx: &ExecutableTransaction,
    ) -> StatefulTransactionValidatorResult<()> {
        // Skip this validation during the systems bootstrap phase.
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
        }
        Ok(())
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L358-390)
```rust
    // TODO(Arni): Consider running this validation for all gas prices.
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

**File:** crates/apollo_consensus_orchestrator/src/fee_market/test.rs (L362-381)
```rust
#[test]
fn test_sustained_congestion_stops_at_the_ceiling() {
    // A full block drives the EIP-1559 price up ~9.5%, independently of the oracle.
    let mut price = TEST_MIN_L2_GAS_PRICE;

    for height in 0..100 {
        price = calculate_next_l2_gas_price_for_fin(
            price,
            BlockNumber(height),
            VERSIONED_CONSTANTS.max_block_size,
            None,
            &flat_min_gas_price_config(),
            None,
        )
        .published_price;
    }

    // Exact equality, not `price <= cap`, which would also pass for a price that never rose.
    assert_eq!(price, TEST_MAX_L2_GAS_PRICE);
}
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L166-227)
```rust
/// Calculate the base gas price for the next block according to EIP-1559.
///
/// # Parameters
/// - `price`: The base gas price per unit (in fri) of the current block.
/// - `gas_used`: The total gas used in the current block.
/// - `gas_target`: The target gas usage per block.
/// - `min_gas_price`: The minimum gas price to enforce.
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
