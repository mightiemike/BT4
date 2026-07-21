### Title
`FeeEstimation` RPC Response Missing `l2_gas_consumed` Causes Authoritative-Looking Wrong Fee Breakdown for V3 Transactions - (`File: crates/apollo_rpc_execution/src/objects.rs`)

### Summary

The `FeeEstimation` struct returned by `starknet_estimateFee` and `starknet_simulateTransactions` omits the `l2_gas_consumed` field while still populating `l2_gas_price`. The `overall_fee` value is taken directly from the blockifier receipt and correctly includes L2 gas costs, but the documented formula (`overall_fee = gas_consumed * gas_price + data_gas_consumed * data_gas_price`) is wrong for V3 transactions. Callers receive an internally inconsistent response: the `overall_fee` is higher than what the provided breakdown fields can reconstruct, and they have no way to derive `l2_gas.max_amount` for their resource bounds.

### Finding Description

In `tx_execution_output_to_fee_estimation`, the function reads `gas_vector.l1_gas` and `gas_vector.l1_data_gas` but silently drops `gas_vector.l2_gas`:

```rust
Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(),
    l1_data_gas_price,
    l2_gas_price,                                          // price present
    // l2_gas_consumed is absent                           // amount absent
    overall_fee: tx_execution_output.execution_info.receipt.fee,
    unit: tx_execution_output.price_unit,
})
```

The `overall_fee` is taken from the blockifier receipt, which for V3 (`AllResources`) transactions equals:

```
l1_gas * l1_gas_price + l1_data_gas * l1_data_gas_price + l2_gas * l2_gas_price
```

But both the Rust doc comment and the OpenRPC spec describe `overall_fee` as:

> "equals to gas_consumed\*gas_price + data_gas_consumed\*data_gas_price"

This formula omits the L2 gas term entirely. The struct even carries a developer TODO acknowledging the gap:

```rust
// TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
// close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
```

The response therefore provides `l2_gas_price` (the unit price) with no corresponding consumed-amount field, making the breakdown irreconcilable with `overall_fee` for any V3 transaction that consumes non-zero L2 gas.

### Impact Explanation

Any caller of `starknet_estimateFee` or `starknet_simulateTransactions` for a V3 transaction receives:

1. A correct `overall_fee` (includes L2 gas cost).
2. A breakdown (`gas_consumed`, `l1_gas_price`, `data_gas_consumed`, `l1_data_gas_price`) that sums to a **lower** value than `overall_fee`.
3. `l2_gas_price` with no paired `l2_gas_consumed`.

A caller who follows the documented formula to verify or reconstruct the fee gets a wrong answer. More critically, a caller who uses the estimation to populate `l2_gas.max_amount` in their V3 resource bounds has no basis for that value from the response; setting it too low causes the transaction to revert with "Insufficient max L2Gas", mirroring the external bug where the wrapped call fails because the intermediary cannot handle the return path.

### Likelihood Explanation

Every V3 (`AllResources`) transaction that executes Cairo 1 code consumes non-zero L2 gas. V3 is the current standard transaction version on Starknet. The condition is triggered by normal, unprivileged user activity with no special setup required.

### Recommendation

Add `l2_gas_consumed: Felt` to `FeeEstimation` and populate it in `tx_execution_output_to_fee_estimation`:

```rust
l2_gas_consumed: gas_vector.l2_gas.0.into(),
```

Update the doc comment and the OpenRPC `FEE_ESTIMATE` schema description to reflect the correct three-term formula. Remove the TODO once the field is present.

### Proof of Concept

1. Submit any V3 invoke transaction to `starknet_estimateFee`.
2. Observe the response: `overall_fee` > `gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price` because L2 gas cost is included in `overall_fee` but not in the breakdown.
3. Attempt to set `resource_bounds.l2_gas.max_amount` from the estimation response — no field exists to derive this value, so the caller must guess or use a heuristic.
4. If `l2_gas.max_amount` is set below the actual L2 gas consumed, the transaction reverts with "Insufficient max L2Gas".

**Relevant code locations:**

`FeeEstimation` struct definition (missing `l2_gas_consumed`, wrong doc comment): [1](#0-0) 

`tx_execution_output_to_fee_estimation` dropping `gas_vector.l2_gas`: [2](#0-1) 

OpenRPC spec `FEE_ESTIMATE` description with wrong formula: [3](#0-2)

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

**File:** crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json (L3624-3666)
```json
            "FEE_ESTIMATE": {
                "title": "Fee estimation",
                "type": "object",
                "properties": {
                    "gas_consumed": {
                        "title": "Gas consumed",
                        "description": "The Ethereum gas consumption of the transaction",
                        "$ref": "#/components/schemas/FELT"
                    },
                    "l1_gas_price": {
                        "title": "Gas price",
                        "description": "The gas price (in wei or fri, depending on the tx version) that was used in the cost estimation",
                        "$ref": "#/components/schemas/FELT"
                    },
                    "data_gas_consumed": {
                        "title": "Data gas consumed",
                        "description": "The Ethereum data gas consumption of the transaction",
                        "$ref": "#/components/schemas/FELT"
                    },
                    "l1_data_gas_price": {
                        "title": "Data gas price",
                        "description": "The data gas price (in wei or fri, depending on the tx version) that was used in the cost estimation",
                        "$ref": "#/components/schemas/FELT"
                    },
                    "overall_fee": {
                        "title": "Overall fee",
                        "description": "The estimated fee for the transaction (in wei or fri, depending on the tx version), equals to gas_consumed*gas_price + data_gas_consumed*data_gas_price",
                        "$ref": "#/components/schemas/FELT"
                    },
                    "unit": {
                        "title": "Fee unit",
                        "description": "units in which the fee is given",
                        "$ref": "#/components/schemas/PRICE_UNIT"
                    }
                },
                "required": [
                    "gas_consumed",
                    "l1_gas_price",
                    "data_gas_consumed",
                    "l1_data_gas_price",
                    "overall_fee",
                    "unit"
                ]
```
