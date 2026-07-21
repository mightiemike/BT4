### Title
`FeeEstimation` omits `l2_gas_consumed` while `overall_fee` silently includes L2 gas cost — (`File: crates/apollo_rpc_execution/src/objects.rs`)

### Summary

The `FeeEstimation` struct returned by `starknet_estimateFee`, `starknet_simulateTransactions`, and `starknet_estimateMessageFee` exposes `l2_gas_price` but never exposes `l2_gas_consumed`. The `overall_fee` field is populated from the actual execution receipt and therefore **does** include the L2 gas cost component, but the struct's own doc-comment states the formula as `gas_consumed * gas_price + data_gas_consumed * data_gas_price`, silently omitting the L2 term. Any client that trusts the documented formula to reconstruct or verify the fee will compute a value that is systematically lower than the real fee for every V3 `AllResources` transaction.

### Finding Description

In `crates/apollo_rpc_execution/src/objects.rs` the `FeeEstimation` struct is:

```rust
pub struct FeeEstimation {
    pub gas_consumed: Felt,          // l1_gas
    pub l1_gas_price: GasPrice,
    pub data_gas_consumed: Felt,     // l1_data_gas
    pub l1_data_gas_price: GasPrice,
    // TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
    // close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
    pub l2_gas_price: GasPrice,      // price present, consumed amount absent
    pub overall_fee: Fee,
    pub unit: PriceUnit,
}
``` [1](#0-0) 

The builder function `tx_execution_output_to_fee_estimation` populates the struct as follows:

```rust
Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(),
    l1_data_gas_price,
    l2_gas_price,
    overall_fee: tx_execution_output.execution_info.receipt.fee,  // includes l2 gas cost
    unit: tx_execution_output.price_unit,
})
``` [2](#0-1) 

`gas_vector.l2_gas` is read from the receipt but never placed into the response. The `overall_fee` is taken directly from `receipt.fee`, which is computed by the blockifier using the full `GasVector` including `l2_gas`. For a V3 `AllResources` transaction the fee is:

```
fee = l1_gas * l1_gas_price + l1_data_gas * l1_data_gas_price + l2_gas * (l2_gas_price + tip)
``` [3](#0-2) 

The struct's doc-comment explicitly states the wrong formula (`gas_consumed*gas_price + data_gas_consumed*data_gas_price`), making the response authoritative-looking but incorrect for any transaction where `l2_gas > 0`.

The `ValidResourceBounds::AllResources` path (Starknet ≥ 0.13.3) is the default for all new V3 transactions submitted through the gateway. [4](#0-3) 

### Impact Explanation

**High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

A caller invoking `starknet_estimateFee` receives:
- A correct `overall_fee` (includes L2 gas cost)
- A correct `l2_gas_price`
- No `l2_gas_consumed`
- A doc-comment formula that says `overall_fee = gas_consumed*l1_gas_price + data_gas_consumed*l1_data_gas_price`

Any client that:
1. Uses the documented formula to verify the fee will compute a value lower than `overall_fee` and conclude the node is overcharging.
2. Uses the breakdown fields to set `resource_bounds` for a follow-up transaction will set `l2_gas` bounds to zero (since no consumed amount is returned), causing the transaction to be rejected or reverted at execution time with `MaxGasAmountExceeded`.
3. Builds a fee-estimation UI will display an incorrect fee breakdown, showing L2 gas as free.

### Likelihood Explanation

Every V3 `AllResources` transaction (the default since Starknet 0.13.3) that consumes any L2 gas triggers this discrepancy. L2 gas is consumed by every Cairo 1 contract execution. The RPC endpoints `starknet_estimateFee` and `starknet_simulateTransactions` are called by every wallet and SDK before submitting a transaction.

### Recommendation

1. Add `l2_gas_consumed: Felt` to `FeeEstimation` and populate it from `gas_vector.l2_gas.0.into()` in `tx_execution_output_to_fee_estimation`.
2. Correct the doc-comment on `overall_fee` to include the L2 gas term.
3. Update the OpenAPI schema `FEE_ESTIMATE` in `crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json` to add `l2_gas_consumed` as a required field.

### Proof of Concept

1. Submit a V3 `AllResources` invoke transaction to `starknet_estimateFee`.
2. Observe the response: `l2_gas_price` is non-zero, `overall_fee` is non-zero, but no `l2_gas_consumed` field is present.
3. Compute `gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price` using the returned fields.
4. Compare to `overall_fee`: the computed value will be strictly less than `overall_fee` by exactly `l2_gas * l2_gas_price` (plus tip contribution), confirming the L2 gas component is silently included in the fee but not exposed in the breakdown.

The TODO comment at line 104 of `objects.rs` is the developer acknowledgment of this gap:

```
// TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
// close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
``` [5](#0-4)

### Citations

**File:** crates/apollo_rpc_execution/src/objects.rs (L94-113)
```rust
#[derive(Debug, Serialize, Deserialize, PartialEq, Eq, Clone)]
pub struct FeeEstimation {
    /// Gas consumed by this transaction. This includes gas for DA in calldata mode.
    pub gas_consumed: Felt,
    /// The gas price for execution and calldata DA.
    pub l1_gas_price: GasPrice,
    /// Gas consumed by DA in blob mode.
    pub data_gas_consumed: Felt,
    /// The gas price for DA blob.
    pub l1_data_gas_price: GasPrice,
    // TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
    // close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
    /// The L2 gas price for execution.
    pub l2_gas_price: GasPrice,
    /// The total amount of fee. This is equal to:
    /// gas_consumed * gas_price + data_gas_consumed * data_gas_price.
    pub overall_fee: Fee,
    /// The unit in which the fee was paid (Wei/Fri).
    pub unit: PriceUnit,
}
```

**File:** crates/apollo_rpc_execution/src/objects.rs (L161-183)
```rust
pub(crate) fn tx_execution_output_to_fee_estimation(
    tx_execution_output: &TransactionExecutionOutput,
    block_context: &BlockContext,
) -> ExecutionResult<FeeEstimation> {
    let gas_prices = &block_context.block_info().gas_prices;
    let (l1_gas_price, l1_data_gas_price, l2_gas_price) = (
        gas_prices.l1_gas_price(&tx_execution_output.price_unit.into()).get(),
        gas_prices.l1_data_gas_price(&tx_execution_output.price_unit.into()).get(),
        gas_prices.l2_gas_price(&tx_execution_output.price_unit.into()).get(),
    );

    let gas_vector = tx_execution_output.execution_info.receipt.gas;

    Ok(FeeEstimation {
        gas_consumed: gas_vector.l1_gas.0.into(),
        l1_gas_price,
        data_gas_consumed: gas_vector.l1_data_gas.0.into(),
        l1_data_gas_price,
        l2_gas_price,
        overall_fee: tx_execution_output.execution_info.receipt.fee,
        unit: tx_execution_output.price_unit,
    })
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L398-413)
```rust
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => l1_gas
                .max_amount
                .saturating_mul(l1_gas.max_price_per_unit)
                .saturating_add(
                    l2_gas
                        .max_amount
                        .saturating_mul(l2_gas.max_price_per_unit.saturating_add(tip.into())),
                )
                .saturating_add(
                    l1_data_gas.max_amount.saturating_mul(l1_data_gas.max_price_per_unit),
                ),
        }
```
