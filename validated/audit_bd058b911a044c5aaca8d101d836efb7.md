### Title
`tx_execution_output_to_fee_estimation` Silently Drops `l2_gas_consumed`, Making `overall_fee` Unverifiable from Breakdown Fields - (File: `crates/apollo_rpc_execution/src/objects.rs`)

### Summary
The `FeeEstimation` struct returned by `starknet_estimateFee` and `starknet_simulateTransactions` exposes `l2_gas_price` but silently omits `l2_gas_consumed`. The `overall_fee` field is set from the actual receipt and correctly includes L2 gas costs, but the struct's own documented formula (`overall_fee = gas_consumed * gas_price + data_gas_consumed * data_gas_price`) does not account for L2 gas. Any client that reconstructs the fee from the breakdown fields will compute a value strictly lower than the actual `overall_fee` for every V3 transaction that consumes non-zero L2 gas.

### Finding Description

In `tx_execution_output_to_fee_estimation` the full `GasVector` is available:

```rust
let gas_vector = tx_execution_output.execution_info.receipt.gas;
```

but only two of its three components are forwarded to the response:

```rust
Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),       // L1 gas ✓
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(), // L1 data gas ✓
    l1_data_gas_price,
    l2_gas_price,                                   // price present …
    overall_fee: tx_execution_output.execution_info.receipt.fee, // includes L2 cost
    unit: tx_execution_output.price_unit,
})
```

`gas_vector.l2_gas` is silently dropped. [1](#0-0) 

The `FeeEstimation` struct itself carries a TODO that acknowledges the gap:

```rust
// TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
// close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
/// The L2 gas price for execution.
pub l2_gas_price: GasPrice,
/// The total amount of fee. This is equal to:
/// gas_consumed * gas_price + data_gas_consumed * data_gas_price.
pub overall_fee: Fee,
``` [2](#0-1) 

The documented formula is canonically wrong for any V3 (`AllResources`) transaction: `overall_fee` is taken from `receipt.fee`, which is computed as `l1_gas × l1_gas_price + l2_gas × l2_gas_price + l1_data_gas × l1_data_gas_price`. The formula omits the middle term entirely.

The analog to the original bug is exact: in the OVM case, a resource budget was not zeroed when a limit was exceeded, so subsequent operations could continue consuming it. Here, the L2 gas resource is consumed and charged (it appears in `overall_fee`) but is never surfaced in the breakdown fields, so the serialization boundary between internal execution and the public RPC response loses the L2 gas dimension. The `l2_gas_price` field is present (analogous to the limit being tracked) while `l2_gas_consumed` is absent (analogous to the budget not being decremented), making the response internally inconsistent.

### Impact Explanation

**High — RPC fee estimation and simulation return an authoritative-looking wrong value.**

A client that trusts the documented formula and computes `gas_consumed × l1_gas_price + data_gas_consumed × l1_data_gas_price` will obtain a fee strictly lower than `overall_fee` for every V3 transaction with non-zero L2 gas. The divergence grows with L2 gas consumption. Concretely:

- Wallets or SDKs that derive `max_amount` bounds from the breakdown (rather than from `overall_fee` directly) will set bounds too low, causing transactions to revert post-execution with `Insufficient max L2Gas`.
- Simulation traces (`starknet_simulateTransactions`) embed the same `FeeEstimation`, so simulated-then-submitted flows are equally affected.
- The presence of `l2_gas_price` with no matching `l2_gas_consumed` makes the response look complete, masking the omission.

### Likelihood Explanation

Every V3 (`AllResources`) transaction that executes Cairo 1 code consumes non-zero L2 (Sierra) gas. This is the standard transaction type for all new Starknet accounts and contracts. The condition is therefore triggered by any ordinary user transaction submitted through the gateway.

### Recommendation

1. Add `l2_gas_consumed: Felt` to `FeeEstimation`.
2. In `tx_execution_output_to_fee_estimation`, set it to `gas_vector.l2_gas.0.into()`.
3. Update the doc-comment formula to `overall_fee = gas_consumed × l1_gas_price + data_gas_consumed × l1_data_gas_price + l2_gas_consumed × l2_gas_price`.
4. Remove the TODO once the field is present.

### Proof of Concept

1. Submit any V3 invoke transaction with `AllResources` bounds against a Cairo 1 contract.
2. Call `starknet_estimateFee`; receive a `FeeEstimation` response.
3. Compute `reconstructed = gas_consumed × l1_gas_price + data_gas_consumed × l1_data_gas_price`.
4. Observe `reconstructed < overall_fee`; the gap equals `gas_vector.l2_gas × l2_gas_price`, which is silently dropped at [3](#0-2) .
5. A client that uses `reconstructed` to set `l2_gas` resource bounds will set them to zero (since `l2_gas_consumed` is absent), causing the transaction to revert with `Insufficient max L2Gas` when submitted.

### Citations

**File:** crates/apollo_rpc_execution/src/objects.rs (L104-113)
```rust
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

**File:** crates/apollo_rpc_execution/src/objects.rs (L172-182)
```rust
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
```
