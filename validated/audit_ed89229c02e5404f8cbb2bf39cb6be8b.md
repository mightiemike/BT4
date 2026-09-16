### Title
Declare transactions bypass `max_l2_gas_amount` validation, allowing a crafted `l2_gas` max-amount + `tip` combination to trigger an uncaught `panic!` (u128 multiplication/addition overflow) in `GasVector::cost` during fee computation - ([File: crates/starknet_api/src/execution_resources.rs])

### Summary
The external report describes an unbounded, user-controlled numeric parameter (`digits`) that is validated only with a lower bound, letting it reach an arithmetic operation (`10 ** digits`) that silently overflows to `0`, producing an unrecoverable `DivisionByZeroError` that is not caught by normal exception handling. The sequencer contains an analogous pattern: `resource_bounds.l2_gas.max_amount` (a user-controlled `u64` field of a V3 transaction) is validated with an upper bound in `StatelessTransactionValidator::validate_resource_bounds`, but that bound is explicitly skipped for `Declare` transactions: [1](#0-0) 

This unbounded `l2_gas.max_amount`, together with an unvalidated `tip` field, is later fed into `GasVector::cost`, which computes `gas.checked_mul(price)` and panics (rather than returning an error) if the multiplication overflows `u128`: [2](#0-1) 

### Finding Description
`StatelessTransactionValidator::validate_resource_bounds` enforces `resource_bounds.l2_gas.max_amount.0 <= self.config.max_l2_gas_amount` for Invoke and DeployAccount transactions, but for Declare transactions this check is skipped entirely (with a `TODO` comment acknowledging the gap): [3](#0-2) 

There is no corresponding upper-bound validation on the transaction `tip` field anywhere in the stateless validator file (only `min_gas_price` is checked against `max_price_per_unit`).

Consequently, a Declare transaction can be submitted with `resource_bounds.l2_gas.max_amount = GasAmount::MAX` (`u64::MAX`, ~1.8×10¹⁹) and an arbitrarily large `tip` (also up to `u64::MAX`). When the sequencer computes the transaction's fee via `get_fee_by_gas_vector` → `GasVector::cost`: [4](#0-3) 
the effective L2 gas price becomes `l2_gas_price + tip` (both cast to `u128`), and is then multiplied by `l2_gas` amount: [5](#0-4) 
With `l2_gas` near `u64::MAX` and `tipped_l2_gas_price` also large (elevated by the attacker-controlled `tip`), the product `gas * price` in `u128` arithmetic can overflow, hitting the `unwrap_or_else(|| panic!(...))` branch instead of returning a `Result`/`Error` that calling code can gracefully reject.

This mirrors the OTPHP bug class exactly: a numeric input is bounds-checked only partially (one code path exempted, similar to the missing upper bound on `digits`), and the unchecked value flows into an arithmetic operation whose overflow condition triggers an unrecoverable runtime error (`panic!` in Rust ≈ uncatchable `Error` in PHP) rather than a handled validation failure.

### Impact Explanation
`get_fee_by_gas_vector`/`GasVector::cost` is invoked during normal transaction pre-validation and fee-charging in the blockifier, which runs identically on every node during block execution and Starknet OS re-execution. A `panic!` triggered here during block building or execution is not a normal `Result`-based rejection — it is a hard process abort/unwind at a point that (depending on call-site panic handling) can crash the sequencer's block-production/execution task. Because block execution is deterministic and mandatory for all validating nodes, a single crafted Declare transaction accepted into the mempool (or included in a block) could cause every honest node that attempts to execute it to panic, halting block production and preventing the network from confirming new transactions — the exact "network unable to confirm new transactions" impact category the validation criteria calls out as acceptable Critical/High impact.

### Likelihood Explanation
The prerequisite fields (`l2_gas.max_amount`, `tip`) are both directly attacker-controlled in any V3 Declare transaction, requiring no special privileges beyond being any unprivileged transaction sender who can submit a Declare transaction (the gateway explicitly does not check `max_l2_gas_amount` for Declare). Whether the overflow is actually reachable in practice depends on: (1) whether some other stateful/protocol-level bound clamps `tip` or `l2_gas.max_amount` before fee computation (I did not find such a check in the reviewed gateway/mempool/blockifier code, but I could not exhaustively verify every validation layer, e.g., mempool-specific tip caps), and (2) whether the panic actually propagates to crash node execution versus being caught by an outer panic boundary (e.g., `catch_unwind` around transaction execution) — I did not find evidence of such a catch boundary in the reviewed code, but this is not something I could fully confirm from the available search results.

### Recommendation
- Remove the Declare-transaction exemption in `validate_resource_bounds` and enforce `max_l2_gas_amount` uniformly across all transaction types (addressing the existing `TODO`).
- Add an explicit upper bound validation on the `tip` field in the stateless transaction validator, consistent with the Starknet OS's own implicit assumption that `tip` fits in a bounded range (the OS Cairo code asserts `tip <= 2**64 - 1`, but that is not a meaningful economic bound).
- Replace the `panic!`-based overflow handling in `GasVector::cost` (and related `checked_mul`/`checked_add` `unwrap_or_else(|| panic!(...))` call sites in `crates/starknet_api/src/execution_resources.rs` and `crates/blockifier/src/fee/`) with a `Result`-returning path so that fee-overflow conditions become a rejected/invalid transaction rather than an uncaught panic during block execution.

### Proof of Concept
1. Submit an `RpcDeclareTransaction::V3` with:
   - `resource_bounds.l2_gas.max_amount = GasAmount(u64::MAX)` (allowed since Declare transactions skip the `max_l2_gas_amount` check at [1](#0-0) ).
   - `tip = Tip(u64::MAX)` (no validator rejects this value).
2. The transaction passes `StatelessTransactionValidator::validate` (resource bounds are non-zero and `max_price_per_unit >= min_gas_price`).
3. During pre-validation/fee estimation or execution, `get_fee_by_gas_vector` is called with this transaction's gas vector and tip [4](#0-3) , which calls `GasVector::cost`.
4. Inside `cost`, `tipped_l2_gas_price = l2_gas_price + tip` and `gas.checked_mul(price.get())` for the L2 gas resource overflow `u128`, hitting the panic branch: [6](#0-5)  — crashing the executing node's transaction-processing task instead of returning a rejected-transaction error.

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-88)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }

        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }

        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }

        Ok(())
    }
```

**File:** crates/starknet_api/src/execution_resources.rs (L155-186)
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
