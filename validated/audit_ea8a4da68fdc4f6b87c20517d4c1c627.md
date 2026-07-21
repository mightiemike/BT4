### Title
Missing `l2_gas_consumed` in RPC Fee Estimation Response Causes Clients to Construct Invalid V3 Transactions - (File: `crates/apollo_rpc_execution/src/objects.rs`)

### Summary
`tx_execution_output_to_fee_estimation` populates `FeeEstimation` with L1 gas and L1 data gas consumed but silently drops `l2_gas` from the `GasVector`. The `l2_gas_price` field is present in the response, but `l2_gas_consumed` is absent. Clients using fee estimation to set V3 resource bounds cannot determine the correct L2 gas amount, causing transactions to fail or be mispriced.

### Finding Description
`FeeEstimation` is the struct returned by `starknet_estimateFee` and simulation endpoints:

```rust
pub struct FeeEstimation {
    pub gas_consumed: Felt,        // L1 gas only
    pub l1_gas_price: GasPrice,
    pub data_gas_consumed: Felt,   // L1 data gas only
    pub l1_data_gas_price: GasPrice,
    pub l2_gas_price: GasPrice,    // price present …
    pub overall_fee: Fee,
    pub unit: PriceUnit,
}
```

The construction function reads `gas_vector` from the execution receipt but only maps L1 and L1-data components:

```rust
let gas_vector = tx_execution_output.execution_info.receipt.gas;
Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(),
    l1_data_gas_price,
    l2_gas_price,
    // gas_vector.l2_gas is never read
    overall_fee: tx_execution_output.execution_info.receipt.fee,
    unit: tx_execution_output.price_unit,
})
```

The in-code TODO confirms the gap:
> `// TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.`

`overall_fee` is computed from the full receipt (which includes L2 gas costs), so the total fee is correct, but the per-resource decomposition is incomplete. A client that reads `gas_consumed` and `data_gas_consumed` to fill in V3 resource bounds will set `l2_gas.max_amount = 0` (or some arbitrary guess), because the estimation gives no signal for L2 gas.

### Impact Explanation
**High — RPC fee estimation returns an authoritative-looking wrong value.**

V3 transactions carry three independent resource bounds: `l1_gas`, `l2_gas`, and `l1_data_gas`. The sequencer enforces each bound independently during execution. A client that trusts `starknet_estimateFee` to size its resource bounds:

1. Correctly sizes `l1_gas` and `l1_data_gas` from the returned fields.
2. Has no basis for `l2_gas.max_amount`; any Cairo 1 contract execution consumes L2 gas.
3. If `l2_gas.max_amount` is set to 0 or too low, the transaction is rejected by the blockifier with an out-of-gas error even though the fee estimation reported success.

The `l2_gas_price` field is present in the response, making the response look complete and authoritative, while the critical consumption figure is missing.

### Likelihood Explanation
Every V3 transaction that invokes Cairo 1 contract logic consumes L2 gas. Any wallet, SDK, or dApp that calls `starknet_estimateFee` and uses the result to populate V3 resource bounds is affected. The impact is not theoretical: the sequencer enforces L2 gas bounds at execution time, and a zero or under-estimated bound causes a hard revert.

### Recommendation
Populate `l2_gas_consumed` in `FeeEstimation` from `gas_vector.l2_gas`:

```rust
pub struct FeeEstimation {
    pub gas_consumed: Felt,
    pub l1_gas_price: GasPrice,
    pub data_gas_consumed: Felt,
    pub l1_data_gas_price: GasPrice,
    pub l2_gas_consumed: Felt,     // add this field
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
    l2_gas_consumed: gas_vector.l2_gas.0.into(),   // populate
    l2_gas_price,
    overall_fee: tx_execution_output.execution_info.receipt.fee,
    unit: tx_execution_output.price_unit,
})
```

Expose the field in the RPC serialization layer and update the OpenRPC schema accordingly.

### Proof of Concept

1. Deploy any Cairo 1 contract (all Cairo 1 execution consumes L2 gas).
2. Call `starknet_estimateFee` for an invoke V3 transaction targeting that contract.
3. Observe the response: `l2_gas_price` is non-zero, but no `l2_gas_consumed` field is present.
4. Construct a V3 transaction using the estimation result, setting `l2_gas.max_amount = 0` (the only value derivable from the response).
5. Submit the transaction; it reverts with an out-of-gas error despite the fee estimation having succeeded.

The root cause is at: [1](#0-0) 

where `l2_gas_consumed` is absent from the struct, and at: [2](#0-1) 

where `gas_vector.l2_gas` is read from the receipt but never mapped into the response, confirmed by the TODO comment at line 104.

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
