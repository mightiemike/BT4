### Title
`ValidResourceBounds` protobuf round-trip silently converts `AllResources` to `L1Gas` when l2_gas and l1_data_gas are zero, producing a divergent transaction hash - (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` deserialization uses a zero-value heuristic to decide between the `L1Gas` and `AllResources` variants. Because `get_tip_resource_bounds_hash` hashes a different number of resource elements for each variant (2 vs 3), a transaction submitted as `AllResources` with zero l2_gas and zero l1_data_gas receives a hash computed over 3 elements at ingestion, but after protobuf round-trip the same transaction is deserialized as `L1Gas` and its hash is recomputed over 2 elements — producing a different value. This is the sequencer-native analog of the HTLC "silent failure" pattern: just as the Cairo contract proceeds without checking the ERC20 return value and records state that does not match the actual token movement, the sequencer's conversion layer silently changes the semantic variant of a transaction's resource bounds, causing the hash domain to shift without any error signal.

### Finding Description

`ValidResourceBounds` has two variants with distinct hash preimages:

```rust
// crates/starknet_api/src/transaction/fields.rs:363-366
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds),          // Pre-0.13.3: hashes 2 resource elements
    AllResources(AllResourceBounds), // Post-0.13.3: hashes 3 resource elements
}
```

`get_tip_resource_bounds_hash` branches on this variant:

```rust
// crates/starknet_api/src/transaction_hash.rs:188-210
pub fn get_tip_resource_bounds_hash(...) -> Result<Felt, StarknetApiError> {
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],                          // 2 elements total
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 elements total
        }
    });
    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
```

The serializer emits identical protobuf bytes for both variants when l2_gas and l1_data_gas are zero:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs:471-489
impl From<ValidResourceBounds> for protobuf::ResourceBounds {
    fn from(value: ValidResourceBounds) -> Self {
        match value {
            ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(value.get_l2_bounds().into()),          // ResourceBounds::default() → zero
                l1_data_gas: Some(ResourceBounds::default().into()), // zero
            },
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
                protobuf::ResourceBounds {
                    l1_gas: Some(l1_gas.into()),
                    l2_gas: Some(l2_gas.into()),         // zero when l2_gas == 0
                    l1_data_gas: Some(l1_data_gas.into()), // zero when l1_data_gas == 0
                },
        }
    }
}
```

The deserializer then applies a zero-check heuristic that irreversibly collapses `AllResources` into `L1Gas`:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs:417-436
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // silent default
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)                     // variant changed silently
        } else {
            ValidResourceBounds::AllResources(...)
        })
    }
}
```

The attack path:

1. A user submits `RpcInvokeTransactionV3` with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`.
2. `convert_rpc_tx_to_internal` calls `InternalRpcInvokeTransactionV3::resource_bounds()` which always returns `ValidResourceBounds::AllResources(...)`, so the hash H₁ is computed over 3 resource elements and stored in `InternalRpcTransaction.tx_hash`.
3. The transaction is serialized to protobuf for P2P state sync. The `Transaction::Invoke(InvokeTransactionV3)` path uses `ValidResourceBounds` (not `AllResourceBounds`), so the round-trip goes through `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`.
4. The receiving node deserializes the transaction: l2_gas=0 and l1_data_gas=0 → `ValidResourceBounds::L1Gas(X)`.
5. `validate_transaction_hash` recomputes the hash H₂ over 2 resource elements. H₁ ≠ H₂.
6. The receiving node rejects the block or records an incorrect state.

Additionally, the deserialized `L1Gas` variant causes `get_gas_vector_computation_mode()` to return `NoL2Gas` instead of `All`, altering fee validation and resource accounting for the re-executed transaction.

### Impact Explanation

**High/Critical.** The transaction hash computed at ingestion (`AllResources`, 3-element Poseidon preimage) diverges from the hash recomputed after protobuf round-trip (`L1Gas`, 2-element preimage). Any node that validates the hash of a synced transaction — including `validate_transaction_hash` in the state sync path — will see a mismatch and either reject a valid block or accept an inconsistent state. The secondary effect is that the wrong `GasVectorComputationMode` is applied during re-execution, producing incorrect fee and resource accounting with direct economic impact.

### Likelihood Explanation

**Medium.** The trigger condition — `AllResources` with l2_gas=0 and l1_data_gas=0 — is reachable by any user submitting a transaction with no L2 or data-gas bounds (e.g., fee-enforcement-disabled transactions, which are explicitly supported by `AllResourceBounds::new_unlimited_gas_no_fee_enforcement()` and used in production operator flows). The TODO comment `// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.` confirms the lossy path is intentional for backward compatibility but has not been gated off.

### Recommendation

1. **Preserve the variant tag across serialization.** Add an explicit discriminant field to the protobuf `ResourceBounds` message (e.g., `bool is_all_resources`) so the deserializer can reconstruct the correct variant without relying on zero-value inference.
2. **Alternatively, always deserialize as `AllResources` when all three fields are present.** The zero-check heuristic should only apply when `l1_data_gas` is truly absent (`None`), not when it is present but zero.
3. **Enforce the TODO.** Once support for 0.13.2 is dropped, replace `unwrap_or_default()` with `ok_or(DEPRECATED_RESOURCE_BOUNDS_ERROR)?` (the constant already defined in `rpc_transaction.rs`) and remove the zero-check branch entirely.

### Proof of Concept

```
// Step 1: Ingestion — hash computed as AllResources (3 elements)
let bounds = AllResourceBounds {
    l1_gas:      ResourceBounds { max_amount: GasAmount(100), max_price_per_unit: GasPrice(1) },
    l2_gas:      ResourceBounds { max_amount: GasAmount(0),   max_price_per_unit: GasPrice(0) },
    l1_data_gas: ResourceBounds { max_amount: GasAmount(0),   max_price_per_unit: GasPrice(0) },
};
// InternalRpcInvokeTransactionV3::resource_bounds() returns AllResources(bounds)
// get_tip_resource_bounds_hash hashes [tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat]
// → H1

// Step 2: Protobuf serialization
// ValidResourceBounds::AllResources → protobuf::ResourceBounds { l1_gas=100/1, l2_gas=0/0, l1_data_gas=0/0 }

// Step 3: Protobuf deserialization (state sync path)
// l1_data_gas.is_zero() && l2_gas.is_zero() == true
// → ValidResourceBounds::L1Gas(ResourceBounds { max_amount: 100, max_price_per_unit: 1 })

// Step 4: Hash recomputation
// get_tip_resource_bounds_hash hashes [tip, L1_GAS_concat, L2_GAS_concat]  (no L1_DATA_GAS)
// → H2

// H1 ≠ H2  →  validate_transaction_hash returns false  →  block rejected / state inconsistency
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-489)
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

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L29-30)
```rust
const DEPRECATED_RESOURCE_BOUNDS_ERROR: ProtobufConversionError =
    ProtobufConversionError::MissingField { field_description: "ResourceBounds::l1_data_gas" };
```
