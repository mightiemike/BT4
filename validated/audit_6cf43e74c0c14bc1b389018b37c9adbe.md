### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas` When l2_gas and l1_data_gas Are Zero, Causing Transaction Hash Mismatch Across P2P Sync Boundary - (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation uses a value-based heuristic to reconstruct the `ValidResourceBounds` variant. When both `l2_gas` and `l1_data_gas` are zero, it always produces `ValidResourceBounds::L1Gas`, even if the original transaction was a post-0.13.3 `AllResources` V3 transaction. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` resource name bytes in the hash preimage only for `AllResources`, a V3 transaction with zero l2_gas and l1_data_gas will produce a different transaction hash before vs. after protobuf serialization/deserialization. This is the direct Sequencer analog of the external M-07 bug: a proportional value (the hash preimage) is computed using one basis (`AllResources`, which includes `L1_DATA_GAS`), but after crossing the serialization boundary the same data is interpreted under a different basis (`L1Gas`, which excludes `L1_DATA_GAS`), producing a divergent output.

### Finding Description

**Root cause — lossy variant reconstruction:** [1](#0-0) 

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

The protobuf wire format carries no explicit discriminant for the `ValidResourceBounds` variant. The deserializer infers the variant purely from whether the numeric values are zero. A post-0.13.3 `AllResources` transaction where the user set `l2_gas = {max_amount: 0, max_price_per_unit: 0}` and `l1_data_gas = {max_amount: 0, max_price_per_unit: 0}` is indistinguishable from a pre-0.13.3 `L1Gas` transaction after this round-trip.

**Hash preimage divergence:**

`get_tip_resource_bounds_hash` branches on the variant: [2](#0-1) 

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
```

The `L1_DATA_GAS` resource name constant is `b"L1_DATA"` (7 non-zero bytes): [3](#0-2) 

`get_concat_resource` encodes `[0 | resource_name(56 bit) | max_amount(64 bit) | max_price_per_unit(128 bit)]`. Even when `max_amount = 0` and `max_price_per_unit = 0`, the resource name bytes `0x4c315f44415441` are non-zero, so the resulting felt is non-zero. Including it in the Poseidon chain changes the hash.

Therefore:
- **Original hash** (AllResources, computed at gateway): `Poseidon(tip, L1_GAS_felt, L2_GAS_felt, L1_DATA_GAS_felt)`
- **Deserialized hash** (L1Gas, computed after P2P round-trip): `Poseidon(tip, L1_GAS_felt, L2_GAS_felt)`

These are different values.

**Serialization path that preserves the zero values:** [4](#0-3) 

The `From<ValidResourceBounds> for protobuf::ResourceBounds` serializer faithfully emits all three fields (including zero l2_gas and l1_data_gas) for `AllResources`. The deserializer then silently collapses them back to `L1Gas`.

**Where the hash is computed from the internal type:**

`InternalRpcDeclareTransactionV3` and `InternalRpcInvokeTransactionV3` store `AllResourceBounds` directly and always wrap it as `ValidResourceBounds::AllResources` when computing the hash: [5](#0-4) [6](#0-5) 

The gateway-side hash is therefore always computed under `AllResources`. The P2P sync path uses the `Transaction` / `InvokeTransactionV3` type which carries `ValidResourceBounds` and is subject to the lossy deserialization.

### Impact Explanation

A node receiving a block via P2P sync that contains a V3 transaction with zero l2_gas and l1_data_gas will deserialize it as `L1Gas` and compute a transaction hash that differs from the hash stored by the original sequencer. Depending on whether the receiving node verifies the hash:

- **If hash is verified**: the node rejects a valid transaction/block, causing wrong state (block not accepted, chain divergence).
- **If hash is not verified**: the node stores the transaction under a wrong hash, causing wrong receipt, wrong event, and wrong state — a Critical impact matching "Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input."

Additionally, the gas computation mode changes from `GasVectorComputationMode::All` to `GasVectorComputationMode::NoL2Gas`: [7](#0-6) 

This affects fee validation and execution resource accounting on the receiving node.

### Likelihood Explanation

Any user can submit a valid V3 transaction via the RPC gateway with `AllResourceBounds` where `l2_gas` and `l1_data_gas` are both zero (e.g., a transaction that only consumes L1 gas). The gateway accepts it, computes the hash under `AllResources`, and includes it in a block. The bug is triggered automatically during P2P sync of that block. No privileged access is required.

### Recommendation

The protobuf `ResourceBounds` message should carry an explicit discriminant field (e.g., a boolean `is_all_resources`) so the variant can be reconstructed faithfully without inspecting numeric values. Alternatively, the `ValidResourceBounds` enum should be replaced with a single canonical representation for all V3 transactions, and the hash function should be made invariant to the variant when the values are identical (i.e., always include `L1_DATA_GAS` in the hash for V3 transactions regardless of variant).

The TODO comment at line 426 acknowledges this is a known transitional state: [8](#0-7) 

### Proof of Concept

1. User submits an `RpcInvokeTransactionV3` with `resource_bounds = AllResourceBounds { l1_gas: {max_amount: 100, max_price_per_unit: 1}, l2_gas: {0, 0}, l1_data_gas: {0, 0} }`.
2. Gateway calls `convert_rpc_tx_to_internal` → `InternalRpcInvokeTransactionV3` stores `resource_bounds: AllResourceBounds { ... }`.
3. `calculate_transaction_hash` calls `get_invoke_transaction_v3_hash` → `get_tip_resource_bounds_hash` with `ValidResourceBounds::AllResources(...)` → hash preimage includes `L1_DATA_GAS_felt = 0x4c315f44415441_0000...0000` → hash = H₁.
4. Transaction is included in a block. Block is propagated via P2P sync as protobuf.
5. Receiving node deserializes `protobuf::ResourceBounds` via `TryFrom`: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas(l1_gas)`.
6. Receiving node recomputes hash: `get_tip_resource_bounds_hash` with `ValidResourceBounds::L1Gas(...)` → hash preimage does NOT include `L1_DATA_GAS_felt` → hash = H₂ ≠ H₁.
7. The stored `tx_hash` (H₁) does not match the recomputed hash (H₂), causing the receiving node to either reject the block or store a wrong hash.

### Citations

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-490)
```rust
impl From<ValidResourceBounds> for protobuf::ResourceBounds {
    fn from(value: ValidResourceBounds) -> Self {
        match value {
            ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(value.get_l2_bounds().into()),
                l1_data_gas: Some(ResourceBounds::default().into()),
            },
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(l2_gas.into()),
                l1_data_gas: Some(l1_data_gas.into()),
            },
        }
    }
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L49-51)
```rust
const L1_GAS: &ResourceName = b"\0L1_GAS";
const L2_GAS: &ResourceName = b"\0L2_GAS";
const L1_DATA_GAS: &ResourceName = b"L1_DATA";
```

**File:** crates/starknet_api/src/transaction_hash.rs (L188-211)
```rust
pub fn get_tip_resource_bounds_hash(
    resource_bounds: &ValidResourceBounds,
    tip: &Tip,
) -> Result<Felt, StarknetApiError> {
    let l1_resource_bounds = resource_bounds.get_l1_bounds();
    let l2_resource_bounds = resource_bounds.get_l2_bounds();

    // L1 and L2 gas bounds always exist.
    // Old V3 txs always have L2 gas bounds of zero, but they exist.
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];

    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L408-411)
```rust
impl DeclareTransactionV3Trait for InternalRpcDeclareTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L679-683)
```rust
impl From<InternalRpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: InternalRpcInvokeTransactionV3) -> Self {
        Self {
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
```

**File:** crates/starknet_api/src/transaction/fields.rs (L416-421)
```rust
    pub fn get_gas_vector_computation_mode(&self) -> GasVectorComputationMode {
        match self {
            Self::AllResources(_) => GasVectorComputationMode::All,
            Self::L1Gas(_) => GasVectorComputationMode::NoL2Gas,
        }
    }
```
