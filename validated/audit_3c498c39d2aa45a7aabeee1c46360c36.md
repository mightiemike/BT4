### Title
`ValidResourceBounds` Protobuf Deserialization Silently Downgrades `AllResources` to `L1Gas` Variant, Producing a Different Transaction Hash for the Same Signed Payload — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter in `transaction.rs` selects the `L1Gas` variant whenever `l2_gas` and `l1_data_gas` are both zero. The `get_tip_resource_bounds_hash` function in `transaction_hash.rs` produces a **structurally different hash preimage** for `L1Gas` (two resource felts) versus `AllResources` (three resource felts, including `L1_DATA_GAS`). Because every RPC/internal transaction type stores `AllResourceBounds` and always hashes as `AllResources`, a transaction whose bounds happen to have zero L2 and L1-data gas is assigned hash H₁ at ingestion but would be assigned hash H₂ ≠ H₁ whenever the same wire bytes are deserialized through the `Transaction`/`ValidResourceBounds` path (P2P block sync, central feeder-gateway sync, `validate_transaction_hash`).

### Finding Description

**Hash computation at ingestion (RPC → internal path)**

`RpcInvokeTransactionV3` and `InternalRpcInvokeTransactionV3` both carry `resource_bounds: AllResourceBounds`. Their `InvokeTransactionV3Trait` implementation unconditionally wraps the field as `ValidResourceBounds::AllResources(self.resource_bounds)`. [1](#0-0) 

`get_tip_resource_bounds_hash` then appends the `L1_DATA_GAS` felt to the hash chain for `AllResources`: [2](#0-1) 

So for a transaction with `l2_gas = 0, l1_data_gas = 0`, the hash preimage is `[tip, L1_GAS_felt, L2_GAS_felt(0), L1_DATA_GAS_felt(0)]` → hash **H₁**.

**Hash computation after protobuf round-trip (P2P / central-sync path)**

The `Transaction` storage type carries `resource_bounds: ValidResourceBounds`. Its protobuf deserializer applies the zero-check: [3](#0-2) 

When `l2_gas.is_zero() && l1_data_gas.is_zero()`, the result is `ValidResourceBounds::L1Gas(l1_gas)`. `

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/starknet_api/src/transaction_hash.rs (L202-208)
```rust
    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L417-436)
```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let Some(l1_gas) = value.l1_gas else {
            return Err(missing("ResourceBounds::l1_gas"));
        };
        let Some(l2_gas) = value.l2_gas else {
            return Err(missing("ResourceBounds::l2_gas"));
        };
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        let l1_gas: ResourceBounds = l1_gas.try_into()?;
        let l2_gas: ResourceBounds = l2_gas.try_into()?;
        let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
```
