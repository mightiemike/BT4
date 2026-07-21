### Title
Protobuf `ValidResourceBounds` deserialization silently downgrades `AllResources` to `L1Gas` when `l2_gas=0` and `l1_data_gas=0`, producing a divergent transaction hash preimage — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` (used in the P2P sync path for historical transactions) silently converts an `AllResources{l1_gas=X, l2_gas=0, l1_data_gas=0}` transaction into `L1Gas(X)`. Because `get_tip_resource_bounds_hash` hashes a **different number of resource felts** depending on the variant (`L1Gas` → 2 felts; `AllResources` → 3 felts), the hash preimage changes across the P2P boundary. A syncing node that recomputes the transaction hash from the deserialized object obtains a value that diverges from the hash committed in the block, and the RPC layer serves wrong resource-bounds metadata for any such transaction.

---

### Finding Description

**Submission path (gateway):** Every `RpcTransaction` carries `AllResourceBounds` (a concrete struct, not the enum). `InternalRpcInvokeTransactionV3::resource_bounds()` always returns `ValidResourceBounds::AllResources(self.resource_bounds)`. The hash is therefore always computed with the 3-resource-felt preimage (L1_GAS + L2_GAS + L1_DATA_GAS). [1](#0-0) 

**Hash function:** `get_tip_resource_bounds_hash` branches on the variant:

- `ValidResourceBounds::L1Gas(_)` → extends `resource_felts` with **nothing** (empty vec), so only 2 felts are hashed.
- `ValidResourceBounds::AllResources(...)` → appends the L1_DATA_GAS felt, so **3 felts** are hashed. [2](#0-1) 

**P2P sync deserialization:** `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` (used for historical `Transaction` objects synced over P2P) applies the following rule:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(...)
})
``` [3](#0-2) 

A transaction originally submitted as `AllResources{l1_gas=X, l2_gas=0, l1_data_gas=0}` (valid and common — e.g., a transaction that only pays L1 gas) is serialized to protobuf with `l2_gas=Some(0)` and `l1_data_gas=Some(0)`. On the receiving side the condition `l1_data_gas.is_zero() && l2_gas.is_zero()` is true, so the variant is reconstructed as `L1Gas(X)`.

**Serialization round-trip confirms the collision:** The `From<ValidResourceBounds> for protobuf::ResourceBounds` serializer emits `l1_data_gas=Some(zero)` for both `L1Gas` and `AllResources{…, l1_data_gas=0}`, making the two wire representations byte-identical. [4](#0-3) 

The serde/JSON path does **not** have this bug: `TryFrom<DeprecatedResourceBoundsMapping>` only produces `L1Gas` when the `L1_DATA_GAS` key is **absent** from the map; `AllResources` serializes the key even when the value is zero, so the round-trip is lossless there. [5](#0-4) [6](#0-5) 

---

### Impact Explanation

1. **Wrong transaction hash from recomputation.** Any code path that recomputes a transaction hash from the deserialized `Transaction` object (e.g., `validate_transaction_hash`, block re-execution, or the OS hash hint) will produce hash H2 ≠ H1 (the hash committed in the block). This can cause a syncing node to reject a valid block or accept a block whose internal hash consistency check fails.

2. **Wrong RPC response.** The stored `ValidResourceBounds` is `L1Gas`; the RPC layer reads it and returns a transaction whose `resource_bounds` field omits `l1_data_gas`, diverging from what the submitter signed and what the block records. This is an authoritative-looking wrong value from the RPC.

Matching impact scope: *High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value* and *High — Transaction conversion or signature/hash logic binds the wrong hash or type*.

---

### Likelihood Explanation

Any V3 transaction with `l2_gas.max_amount = 0` and `l1_data_gas.max_amount = 0` (and their prices zero) triggers the bug. This is a realistic configuration: client-side-proving transactions set all prices to zero and may set l2_gas amount to zero; legacy-style V3 transactions that only specify L1 gas also fall into this category. No special privilege is required — any user can submit such a transaction to the gateway.

---

### Recommendation

In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, remove the heuristic that downgrades to `L1Gas` based on zero values. Instead, use the **presence or absence of the `l1_data_gas` field** as the discriminant (matching the serde path):

```rust
Ok(if value.l1_data_gas.is_none() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

Additionally, once the TODO comment ("Assert data gas is not none once we remove support for 0.13.2") is resolved, make `l1_data_gas` a required field in the protobuf schema for all post-0.13.3 transaction messages, eliminating the ambiguity entirely.

---

### Proof of Concept

```
1. User submits InvokeV3 with AllResourceBounds {
       l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
       l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
       l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
   }

2. Gateway path:
   InternalRpcInvokeTransactionV3::resource_bounds()
     → ValidResourceBounds::AllResources(...)
   get_tip_resource_bounds_hash hashes: tip | L1_GAS_felt | L2_GAS_felt(0) | L1_DATA_GAS_felt(0)
   → Hash H1 (3 resource felts)

3. Transaction committed to block with hash H1.

4. P2P sync: block is serialized to protobuf.
   From<ValidResourceBounds> for protobuf::ResourceBounds:
     l1_gas = Some(1000/1), l2_gas = Some(0/0), l1_data_gas = Some(0/0)

5. Receiving node deserializes:
   TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
     l1_data_gas.is_zero() && l2_gas.is_zero() → ValidResourceBounds::L1Gas(l1_gas)

6. Receiving node recomputes hash:
   get_tip_resource_bounds_hash hashes: tip | L1_GAS_felt
   → Hash H2 (2 resource felts, L1_DATA_GAS_felt omitted)

7. H1 ≠ H2 → hash verification fails / RPC returns L1Gas resource bounds
   instead of AllResources, diverging from the signed transaction.
``` [7](#0-6) [8](#0-7)

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L417-437)
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

**File:** crates/starknet_api/src/transaction/fields.rs (L551-573)
```rust
impl Serialize for ValidResourceBounds {
    fn serialize<S>(&self, s: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        let map = match self {
            ValidResourceBounds::L1Gas(l1_gas) => BTreeMap::from([
                (Resource::L1Gas, *l1_gas),
                (Resource::L2Gas, ResourceBounds::default()),
            ]),
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => BTreeMap::from([
                (Resource::L1Gas, *l1_gas),
                (Resource::L2Gas, *l2_gas),
                (Resource::L1DataGas, *l1_data_gas),
            ]),
        };
        DeprecatedResourceBoundsMapping(map).serialize(s)
    }
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L575-606)
```rust
impl TryFrom<DeprecatedResourceBoundsMapping> for ValidResourceBounds {
    type Error = StarknetApiError;
    fn try_from(
        resource_bounds_mapping: DeprecatedResourceBoundsMapping,
    ) -> Result<Self, Self::Error> {
        if let (Some(l1_bounds), Some(l2_bounds)) = (
            resource_bounds_mapping.0.get(&Resource::L1Gas),
            resource_bounds_mapping.0.get(&Resource::L2Gas),
        ) {
            match resource_bounds_mapping.0.get(&Resource::L1DataGas) {
                Some(data_bounds) => Ok(Self::AllResources(AllResourceBounds {
                    l1_gas: *l1_bounds,
                    l1_data_gas: *data_bounds,
                    l2_gas: *l2_bounds,
                })),
                None => {
                    if l2_bounds.is_zero() {
                        Ok(Self::L1Gas(*l1_bounds))
                    } else {
                        Err(StarknetApiError::InvalidResourceMappingInitializer(format!(
                            "Missing data gas bounds but L2 gas bound is not zero: \
                             {resource_bounds_mapping:?}",
                        )))
                    }
                }
            }
        } else {
            Err(StarknetApiError::InvalidResourceMappingInitializer(format!(
                "{resource_bounds_mapping:?}",
            )))
        }
    }
```
