### Title
`starknet_estimateFee` omits `l2_gas_consumed` from response, making `overall_fee` description wrong and preventing correct L2 gas bound derivation — (`File: crates/apollo_rpc_execution/src/objects.rs`)

### Summary

`starknet_estimateFee` returns a `FeeEstimation` object that exposes `gas_consumed` (L1 gas) and `data_gas_consumed` (L1 data gas) but silently omits `l2_gas_consumed`. The `overall_fee` field is documented as equalling `gas_consumed * gas_price + data_gas_consumed * data_gas_price`, but for V3 (`AllResourceBounds`) transactions it actually includes L2 gas costs too. A caller who uses the returned breakdown to populate `l2_gas.max_amount` in a subsequent V3 transaction has no basis for that field and will have the transaction reverted by the blockifier with "Insufficient max L2Gas".

### Finding Description

`FeeEstimation` is defined in `crates/apollo_rpc_execution/src/objects.rs`:

```rust
pub struct FeeEstimation {
    pub gas_consumed: Felt,        // L1 gas
    pub l1_gas_price: GasPrice,
    pub data_gas_consumed: Felt,   // L1 data gas
    pub l1_data_gas_price: GasPrice,
    // TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
    // close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
    pub l2_gas_price: GasPrice,
    /// The total amount of fee. This is equal to:
    /// gas_consumed * gas_price + data_gas_consumed * data_gas_price.
    pub overall_fee: Fee,
    pub unit: PriceUnit,
}
``` [1](#0-0) 

The conversion function that populates this struct reads the actual gas vector from the receipt:

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
``` [2](#0-1) 

`gas_vector.l2_gas` is never written into the response. The `overall_fee` is taken from `receipt.fee`, which is computed by the blockifier as:

```
fee = l1_gas * l1_gas_price + l1_data_gas * l1_data_gas_price + l2_gas * l2_gas_price
```

So `overall_fee` is correct, but the documented formula (`gas_consumed * gas_price + data_gas_consumed * data_gas_price`) is wrong for any V3 transaction that consumes L2 gas. The L2 gas component is silently folded into `overall_fee` with no corresponding `l2_gas_consumed` field.

The OpenRPC schema for `FEE_ESTIMATE` likewise has no `l2_gas_consumed` field: [3](#0-2) 

On the blockifier side, post-execution validation enforces that `l2_gas.max_amount >= actual_l2_gas_consumed`: [4](#0-3) 

A transaction that sets `l2_gas.max_amount` too low is reverted with "Insufficient max L2Gas": [5](#0-4) 

### Impact Explanation

Any caller of `starknet_estimateFee` (or `starknet_simulateTransactions`, which reuses the same `FeeEstimation` struct) who attempts to construct a V3 transaction using the returned breakdown cannot determine the correct `l2_gas.max_amount`. If they set it to zero or derive it from the incomplete breakdown, the blockifier will revert the transaction with "Insufficient max L2Gas" even though the fee estimate appeared to succeed. The `overall_fee` value is numerically correct but its documented formula is wrong, making the response authoritative-looking yet misleading.

This matches the impact category: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

### Likelihood Explanation

Every V3 (`AllResourceBounds`) transaction that consumes non-zero L2 gas is affected. The TODO comment in the source code (`// TODO(Tzahi): Add l2_gas_consumed`) confirms the omission is known but unresolved. Any integrator who follows the documented formula for `overall_fee` to back-calculate resource bounds will produce a transaction that reverts.

### Recommendation

1. Add `l2_gas_consumed: Felt` to `FeeEstimation` and populate it from `gas_vector.l2_gas.0.into()` in `tx_execution_output_to_fee_estimation`.
2. Update the `overall_fee` doc comment to reflect the correct three-component formula.
3. Add `l2_gas_consumed` to the `FEE_ESTIMATE` OpenRPC schema.
4. Resolve the `TODO(Tzahi)` comment.

### Proof of Concept

1. Call `starknet_estimateFee` on any V3 invoke transaction that executes Cairo 1 code (which consumes L2/Sierra gas).
2. Observe the response: `gas_consumed` and `data_gas_consumed` are present; `l2_gas_consumed` is absent.
3. Compute `gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price` — this will be less than `overall_fee` by exactly `l2_gas_consumed * l2_gas_price`.
4. Construct a V3 transaction with `l2_gas.max_amount = 0` (the only value derivable from the response without guessing).
5. Submit the transaction — the blockifier reverts it with "Insufficient max L2Gas" even though the fee estimate succeeded.

The discrepancy is acknowledged in the codebase itself: [6](#0-5)

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

**File:** crates/blockifier/src/fee/fee_checks.rs (L128-149)
```rust
    pub fn check_all_gas_amounts_within_bounds(
        max_amount_bounds: &GasVector,
        gas_vector: &GasVector,
    ) -> FeeCheckResult<()> {
        // TODO(Arni): Consider refactoring the returned error. The first failed check will hide
        // future checks.
        for (resource, max_amount, actual_amount) in [
            (L1Gas, max_amount_bounds.l1_gas, gas_vector.l1_gas),
            (L2Gas, max_amount_bounds.l2_gas, gas_vector.l2_gas),
            (L1DataGas, max_amount_bounds.l1_data_gas, gas_vector.l1_data_gas),
        ] {
            if max_amount < actual_amount {
                return Err(FeeCheckError::MaxGasAmountExceeded {
                    resource,
                    max_amount,
                    actual_amount,
                });
            }
        }

        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L1529-1535)
```rust
    assert!(
        tx_execution_info2
            .revert_error
            .unwrap()
            .to_string()
            .contains(&format!("Insufficient max {overdraft_resource}"))
    );
```
