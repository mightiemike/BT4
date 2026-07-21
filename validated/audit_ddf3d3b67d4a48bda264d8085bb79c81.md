### Title
Silent `ValidResourceBounds::AllResources`→`L1Gas` coercion in protobuf deserialization produces a divergent transaction hash preimage - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary
The protobuf `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion in the P2P sync path silently re-classifies an `AllResources` transaction as `L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero. Because `get_tip_resource_bounds_hash` hashes a different number of resource elements depending on the variant, the transaction hash recomputed from the deserialized object diverges from the hash that was committed on-chain, breaking the canonicalization invariant that ties a transaction's signature domain to its stored hash.

---

### Finding Description

**Root cause — protobuf deserialization (`transaction.rs` lines 417–436):**

```rust
// crates/apollo_protobuf/src/converters/transaction.rs
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)          // ← type silently changed
        } else {
            ValidResourceBounds::AllResources(...)
        })
    }
}
```

A V3 transaction that was originally created and signed with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is a valid, well-formed transaction. Its on-chain hash was computed with the `AllResources` branch of `get_tip_resource_bounds_hash`. After a protobuf round-trip through the P2P sync path, the deserialized object carries `L1Gas(X)` instead.

**Hash domain divergence (`transaction_hash.rs` lines 187–211):**

```rust
// crates/starknet_api/src/transaction_hash.rs
pub fn get_tip_resource_bounds_hash(
    resource_bounds: &ValidResourceBounds,
    tip: &Tip,
) -> Result<Felt, StarknetApiError> {
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],                          // 2 resources
        ValidResourceBounds::AllResources(all) => {
            vec![get_concat_resource(&all.l1_data_gas, L1_DATA_GAS)?]    // 3 resources
        }
    });
    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
```

| Variant at signing time | Elements hashed | Resulting hash |
|---|---|---|
| `AllResources { l2_gas=0, l1_data_gas=0 }` | `tip, L1_GAS, L2_GAS=0, L1_DATA_GAS=0` | **H₁** |
| `L1Gas` (same l1_gas, after round-trip) | `tip, L1_GAS, L2_GAS=0` | **H₂ ≠ H₁** |

The `L1_DATA_GAS=0` element is present in H₁ but absent in H₂. Poseidon is collision-resistant, so H₁ ≠ H₂ with overwhelming probability.

**Serialization direction is lossless, deserialization is not:**

```rust
// From<ValidResourceBounds> for protobuf::ResourceBounds — always emits all three fields
ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
    l1_gas: Some(l1_gas.into()),
    l2_gas: Some(value.get_l2_bounds().into()),       // zero
    l1_data_gas: Some(ResourceBounds::default().into()), // zero
},
```

The serializer faithfully encodes all