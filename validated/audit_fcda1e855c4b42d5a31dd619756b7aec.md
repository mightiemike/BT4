### Title
Missing `l2_gas_consumed` in `FeeEstimation` Response Causes Irreconcilable Fee Breakdown — (File: `crates/apollo_rpc_execution/src/objects.rs`)

---

### Summary

`tx_execution_output_to_fee_estimation` silently drops the `l2_gas` component from the `FeeEstimation` response. The `overall_fee` field is populated from the actual execution receipt — which **does** include L2 gas costs — but `l2_gas_consumed` is never surfaced. The result is that the fee breakdown returned by `starknet_estimateFee` / `starknet_simulateTransactions` is internally inconsistent: `l2_gas_price` is present but its paired quantity is absent, and the `overall_fee` cannot be reconstructed from the other fields.

---

### Finding Description

`FeeEstimation` exposes three gas dimensions but only populates two of them:

```rust
// crates/apollo_rpc_execution/src/objects.rs  lines 94-113
pub struct FeeEstimation {
    pub gas_consumed: Felt,        // ← l1_gas
    pub l1_gas_price: GasPrice,
    pub data_gas_consumed: Felt,   // ← l1_data_gas
    pub l1_data_gas_price: GasPrice,
    // TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of
    // l1_gas_price only is close enough (as there are roundings) to the fee
    // of both l1_gas_price and l2_gas_price.
    pub l2_gas_price: GasPrice,    // ← price present, quantity absent
    pub overall_fee: Fee,
    pub unit: PriceUnit,
}
``` [1](#0-0) 

The builder function receives a full `GasVector` (`l1_gas`, `l1_data_gas`, `l2_gas`) but maps only the first two:

```rust
// lines 172-182
let gas_vector = tx_execution_output.execution_info.receipt.gas;

Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(),
    l1_data_gas_price,
    l2_gas_price,
    // gas_vector.l2_gas is silently dropped here
    overall_fee: tx_execution_output.execution_info.receipt.fee,
    unit: tx_execution_output.price_unit,
})
``` [2](#0-1) 

`overall_fee` is taken directly from `receipt.fee`, which is the actual fee charged by the blockifier and **includes** the L2 gas component (`l2_gas * l2_gas_price`). The OpenRPC spec, however, describes `overall_fee` as:

> *"equals to gas_consumed\*gas_price + data_gas_consumed\*data_gas_price"* [3](#0-2) 

That formula omits L2 gas entirely. For any V3 transaction that consumes L2 gas (the normal case since Starknet 0.13.3), the formula yields a value strictly less than `overall_fee`.

---

### Impact Explanation

**High — RPC fee estimation returns an authoritative-looking wrong value.**

A client that calls `starknet_estimateFee` or `starknet_simulateTransactions` receives:
- `gas_consumed` (L1 gas units)
- `data_gas_consumed` (L1 data gas units)
- `l2_gas_price` (price per L2 gas unit)
- `overall_fee` (actual total fee, including L2 gas)

Because `l2_gas_consumed` is absent, the client **cannot** reconstruct `overall_fee` from the returned fields. Any wallet, SDK, or dApp that applies the spec formula:

```
reconstructed_fee = gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price
```

will compute a value that is **lower than the actual fee charged**, by exactly `l2_gas_consumed * l2_gas_price`. The discrepancy grows with transaction complexity. The `l2_gas_price` field is rendered meaningless without its paired quantity.

---

### Likelihood Explanation

Every V3 Invoke, Declare, or DeployAccount transaction on Starknet ≥ 0.13.3 consumes L2 gas. The missing field affects all fee estimation and simulation calls for these transaction types, which represent the entire modern transaction surface. No special conditions are required; any unprivileged user calling `starknet_estimateFee` triggers the inconsistency.

---

### Recommendation

Add `l2_gas_consumed` to `FeeEstimation` and populate it in `tx_execution_output_to_fee_estimation`:

```rust
pub struct FeeEstimation {
    pub gas_consumed: Felt,
    pub l1_gas_price: GasPrice,
    pub data_gas_consumed: Felt,
    pub l1_data_gas_price: GasPrice,
    pub l2_gas_consumed: Felt,      // ← add
    pub l2_gas_price: GasPrice,
    pub overall_fee: Fee,
    pub unit: PriceUnit,
}

// in tx_execution_output_to_fee_estimation:
Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(),
    l1_data_gas_price,
    l2_gas_consumed: gas_vector.l2_gas.0.into(),  // ← add
    l2_gas_price,
    overall_fee: tx_execution_output.execution_info.receipt.fee,
    unit: tx_execution_output.price_unit,
})
```

Update the OpenRPC spec description of `overall_fee` to reflect the correct formula:

```
overall_fee = gas_consumed*l1_gas_price
            + data_gas_consumed*l1_data_gas_price
            + l2_gas_consumed*l2_gas_price
```

---

### Proof of Concept

1. Submit any V3 Invoke transaction to `starknet_estimateFee` on a node running this sequencer.
2. Observe the response: `l2_gas_price` is non-zero, but `l2_gas_consumed` is absent.
3. Compute `reconstructed_fee = gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price`.
4. Compare to `overall_fee`. For a transaction consuming, e.g., 740,000 L2 gas at a price of 8,000,000,000 FRI/gas, the L2 component alone is `5,920,000,000,000,000` FRI — a gap that is invisible to the caller but already included in `overall_fee`.

The `gas_vector.l2_gas` field is available at the call site but is never forwarded to the response, directly mirroring the Wormhole pattern where the fee value (`msg.value`) existed at the call site but was not passed through to the downstream function. [4](#0-3)

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

**File:** crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json (L3648-3658)
```json
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
```
