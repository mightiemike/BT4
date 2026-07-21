### Title
Integer Truncation in `validate_tx_l2_gas_price_within_threshold` Lowers Gas Price Admission Threshold Below Configured Minimum — (`crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

The gateway's L2 gas price admission check computes a minimum threshold by multiplying the previous block's L2 gas price by a configured percentage, then calling `.to_integer()` on the resulting `Ratio`. Because `Ratio::to_integer()` performs **floor (truncating) division**, the computed threshold is silently rounded down whenever the product is not exactly divisible by 100. This allows transactions whose gas price is below the operator-intended minimum to pass gateway admission.

---

### Finding Description

In `validate_tx_l2_gas_price_within_threshold`, the threshold is computed as:

```rust
// crates/apollo_gateway/src/stateful_transaction_validator.rs, lines 367–371
let gas_price_threshold_multiplier =
    Ratio::new(self.config.min_gas_price_percentage.into(), 100_u128);
let threshold = (gas_price_threshold_multiplier
    * previous_block_l2_gas_price.get().0)
    .to_integer();
```

`Ratio::to_integer()` from the `num_rational` crate returns the **integer part** of the ratio, which for positive values is the **floor**. When `min_gas_price_percentage * previous_block_l2_gas_price` is not evenly divisible by 100, the threshold is truncated downward.

**Concrete example:**
- `min_gas_price_percentage = 80` (configured as 80%)
- `previous_block_l2_gas_price = 101`
- Exact threshold = `80 * 101 / 100 = 80.8`
- `.to_integer()` = **80** (floor)
- A transaction with `tx_l2_gas_price = 80` satisfies `80 < 80 == false`, so it **passes**
- The operator intended to require at least **81** (ceiling of 80.8)

The maximum precision loss per check is `(100 - 1) / 100 = 0.99` gas price units, but this is systematic and predictable. Any gas price value where `min_gas_price_percentage * price` has a non-zero remainder mod 100 produces a threshold that is 1 unit too low.

The external bug analog is exact: just as `(_range / 2)` in Solidity truncates a fractional tick-spacing divisor and causes the modulo check to pass for values it should reject, `.to_integer()` here truncates a fractional gas price threshold and causes the comparison to pass for prices it should reject.

---

### Impact Explanation

The gateway's `validate_tx_l2_gas_price_within_threshold` is the admission gate that enforces the minimum L2 gas price policy before a transaction enters the mempool. When the threshold is floored, transactions with a gas price strictly below the operator-configured minimum percentage are admitted. This maps directly to:

> **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

An attacker can reliably submit transactions at exactly the floored threshold value to bypass the intended gas price floor, undermining fee market enforcement and potentially enabling spam or economic manipulation at below-policy prices.

---

### Likelihood Explanation

The condition `(min_gas_price_percentage * previous_block_l2_gas_price) % 100 != 0` is satisfied for the vast majority of real gas price values. Since `previous_block_l2_gas_price` is a dynamic, continuously changing value (driven by EIP-1559 mechanics), it will almost never be an exact multiple of `100 / gcd(min_gas_price_percentage, 100)`. The bug is therefore triggered on nearly every block where the check is active. Any user who observes the current block's L2 gas price can trivially compute the floored threshold and submit a transaction at that price.

---

### Recommendation

Replace `.to_integer()` (floor) with ceiling division for the threshold computation. Using `num_rational::Ratio::ceil()` before converting to integer ensures the threshold is never lower than the operator's intent:

```rust
let threshold = (gas_price_threshold_multiplier
    * previous_block_l2_gas_price.get().0)
    .ceil()
    .to_integer();
```

This mirrors the fix pattern used elsewhere in the codebase (e.g., `checked_div_ceil` in `to_discounted_l1_gas`, and `.ceil().numer()` in `convert_l1_to_l2_gas_price_round_up`) where rounding direction is security-relevant.

---

### Proof of Concept

Given:
- `min_gas_price_percentage = 80` (u8, stored in `StatefulTransactionValidatorConfig`)
- `previous_block_l2_gas_price = 101` (NonzeroGasPrice)

Current code path:
1. `Ratio::new(80u128, 100u128)` → `Ratio(4, 5)` (reduced)
2. `Ratio(4, 5) * 101` → `Ratio(404, 5)` = `80.8`
3. `.to_integer()` → **80**
4. Check: `tx_l2_gas_price.0 < 80` — a transaction with price `80` passes (`80 < 80` is `false`)

Intended behavior: threshold should be **81** (⌈80.8⌉), so a transaction with price `80` should be rejected.

The attacker submits `AllResourceBounds { l2_gas: ResourceBounds { max_price_per_unit: GasPrice(80), .. }, .. }` and it is admitted by the gateway despite being below the 80% minimum of the previous block price of 101. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L367-371)
```rust
                let gas_price_threshold_multiplier =
                    Ratio::new(self.config.min_gas_price_percentage.into(), 100_u128);
                let threshold = (gas_price_threshold_multiplier
                    * previous_block_l2_gas_price.get().0)
                    .to_integer();
```

**File:** crates/apollo_gateway_config/src/config.rs (L285-286)
```rust
    // Minimum gas price as percentage of threshold to accept transactions.
    pub min_gas_price_percentage: u8, // E.g., 80 to require 80% of threshold.
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
