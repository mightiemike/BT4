### Title
`FeeEstimation` Missing `l2_gas_consumed` Causes Incorrect `overall_fee` Formula and Incomplete Fee Breakdown for v3 Transactions — (`crates/apollo_rpc_execution/src/objects.rs`)

---

### Summary

The `FeeEstimation` struct returned by `starknet_estimateFee` and `starknet_simulateTransactions` exposes `l2_gas_price` but omits `l2_gas_consumed`. The struct's own doc-comment and the `FEE_ESTIMATE` OpenAPI schema both describe `overall_fee` as equalling `gas_consumed * gas_price + data_gas_consumed * data_gas_price`. For any v3 transaction that incurs non-zero L2 gas, this formula is wrong: it silently drops the `l2_gas_consumed * l2_gas_price` term. The actual `overall_fee` value (taken from the blockifier receipt) is correct, but the authoritative description and the exposed breakdown fields are inconsistent with it, causing callers who reconstruct the fee from the returned fields to compute a value that is lower than the real fee.

---

### Finding Description

`FeeEstimation` in `crates/apollo_rpc_execution/src/objects.rs` is defined as:

```rust
pub struct FeeEstimation {
    pub gas_consumed: Felt,        // L1 gas only
    pub l1_gas_price: GasPrice,
    pub data_gas_consumed: Felt,   // L1 data gas only
    pub l1_data_gas_price: GasPrice,
    // TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of
    // l1_gas_price only is close enough (as there are roundings) to the fee
    // of both l1_gas_price and l2_gas_price.
    pub l2_gas_price: GasPrice,    // price present, consumed amount absent
    pub overall_fee: Fee,
    pub unit: PriceUnit,
}
```

The doc-comment on `overall_fee` states:
> "This is equal to: gas_consumed * gas_price + data_gas_consumed * data_gas_price."

The `FEE_ESTIMATE` schema in `starknet_api_openrpc.json` repeats the same formula and lists no `l2_gas_consumed` or `l2_gas_price` fields.

`tx_execution_output_to_fee_estimation` populates the struct as follows:

```rust
Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(),
    l1_data_gas_price,
    l2_gas_price,
    overall_fee: tx_execution_output.execution_info.receipt.fee,  // correct total
    unit: tx_execution_output.price_unit,
})
```

`gas_vector.l2_gas` is never placed into any returned field. The `overall_fee` is taken from the blockifier receipt and is numerically correct (it includes L2 gas costs). However, the three-component breakdown `(l1_gas, l1_data_gas, l2_gas)` is only partially exposed: `l2_gas_consumed` is absent, so the formula described in both the struct comment and the OpenAPI schema is wrong for any v3 transaction with non-zero L2 gas.

The `FEE_ESTIMATE` schema does not include `l2_gas_price` either, yet the Rust struct serialises it as an extra field. The schema validation test at line 1265–1273 passes only because the schema does not set `additionalProperties: false`, meaning the extra field is silently accepted rather than flagged.

---

### Impact Explanation

Any caller of `starknet_estimateFee` or `starknet_simulateTransactions` that reconstructs the fee from the returned fields using the documented formula:

```
fee = gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price
```

will compute a value that is strictly lower than `overall_fee` for v3 transactions with non-zero L2 gas. The gap equals `l2_gas_consumed * l2_gas_price`. Because `l2_gas_consumed` is not returned, the caller has no way to detect or correct the discrepancy from the response alone.

A wallet or SDK that uses this formula to set `max_fee` or `AllResources` bounds will under-provision the L2 gas bound, causing the sequencer to reject the transaction at pre-validation with `InsufficientResourceBounds`. This matches the "High — RPC fee estimation returns an authoritative-looking wrong value" impact category.

---

### Likelihood Explanation

Every v3 transaction (version `0x3`) executed on a network with non-zero `l2_gas_price` is affected. The Starknet v0.13.3+ fee model makes L2 gas the dominant cost component for Cairo 1 contracts. Any client that follows the documented formula rather than using `overall_fee` directly will be affected on every such transaction.

---

### Recommendation

1. Add `l2_gas_consumed: Felt` to `FeeEstimation` and populate it from `gas_vector.l2_gas.0.into()` in `tx_execution_output_to_fee_estimation`.
2. Update the `overall_fee` doc-comment to read: "equals `gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price + l2_gas_consumed * l2_gas_price`."
3. Add `l2_gas_consumed` and `l2_gas_price` to the `FEE_ESTIMATE` schema in `starknet_api_openrpc.json` so the schema matches the implementation.

---

### Proof of Concept

Submit a v3 invoke transaction to `starknet_estimateFee` on a network where `l2_gas_price > 0`:

1. Receive `FeeEstimation` with `gas_consumed = G1`, `l1_gas_price = P1`, `data_gas_consumed = G_DA`, `l1_data_gas_price = P_DA`, `l2_gas_price = P2`, `overall_fee = F`.
2. Compute `formula_fee = G1 * P1 + G_DA * P_DA`.
3. Observe `formula_fee < F` (the gap is `l2_gas_consumed * P2`, which is non-zero for any Cairo 1 contract execution).
4. Use `formula_fee` as the `max_fee` / L2 gas bound in a follow-up transaction.
5. The sequencer rejects the transaction with `InsufficientResourceBounds` because the actual L2 gas cost exceeds the bound derived from the formula.

The TODO comment at line 104–105 of `crates/apollo_rpc_execution/src/objects.rs` explicitly acknowledges the missing field, confirming the root cause is present in the production code. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_rpc/src/v0_8/execution_test.rs (L1264-1274)
```rust
#[test]
fn validate_fee_estimation_schema() {
    let mut rng = get_rng();
    let fee_estimate = FeeEstimation::get_test_instance(&mut rng);
    let schema = get_starknet_spec_api_schema_for_components(
        &[(SpecFile::StarknetApiOpenrpc, &["FEE_ESTIMATE"])],
        &VERSION,
    );
    let serialized = serde_json::to_value(fee_estimate).unwrap();
    assert!(validate_schema(&schema, &serialized));
}
```
