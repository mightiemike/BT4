### Title
Protobuf `ValidResourceBounds` Conversion Collapses `AllResources` to `L1Gas` When L2 and L1 Data Gas Are Zero, Producing a Different Transaction Hash Across Serialization Boundaries — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` re-derives the enum variant from the *values* of the gas bounds rather than from an explicit type tag. When a post-0.13.3 `AllResources` transaction carries zero `l2_gas` and zero `l1_data_gas`, the protobuf round-trip silently downgrades it to the `L1Gas` variant. Because `get_tip_resource_bounds_hash` hashes a different number of elements for each variant, the transaction hash computed after protobuf deserialization diverges from the hash computed on the original RPC path, breaking the canonicalization invariant that every serialization boundary must preserve the same hash preimage.

---

### Finding Description

**Threshold-crossing analog.** The external bug describes a price calculation that uses the current tier without accounting for crossing a tier boundary when buying in bulk. The sequencer analog is a variant-selection rule that uses the *current values* of gas bounds without accounting for the fact that those values can legitimately be zero in a post-0.13.3 `AllResources` transaction, causing the wrong hash tier to be applied.

**Root cause — protobuf converter (`transaction.rs` lines 417–436):**

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)          // ← pre-0.13.3 path
        } else {
            ValidResourceBounds::AllResources(...)       // ← post-0.13.3 path
        })
    }
}
``` [1](#0-0) 

The decision is made purely on whether the *numeric values* of `l2_gas` and `l1_data_gas` are zero. A post-0.13.3 transaction that legitimately sets both to zero (e.g., a transaction that only consumes L1 gas) is misclassified as the pre-0.13.3 `L1Gas` variant.

**Contrast with the JSON deserialization path (`fields.rs` lines 575–606):**

```rust
match resource_bounds_mapping.0.get(&Resource::L1DataGas) {
    Some(data_bounds) => Ok(Self::AllResources(AllResourceBounds { ... })),  // key present → AllResources
    None => { if l2_bounds.is_zero() { Ok(Self::L1Gas(*l1_bounds)) } ... }
}
``` [2](#0-1) 

The JSON path classifies by *key presence*, not by value. A transaction with `L1DataGas: {0, 0}` present in the JSON map is always `AllResources`. The same transaction after a protobuf round-trip becomes `L1Gas`.

**Hash divergence — `get_tip_resource_bounds_hash` (`transaction_hash.rs` lines 188–211):**

```rust
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
// For new V3 txs, need to also hash the data gas bounds.
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 elements
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 elements
    }
});
``` [3](#0-2) 

`L1Gas` → Poseidon over `[tip, l1_gas_felt, l2_gas_felt]` (3 elements).  
`AllResources` with zero l2/l1_data → Poseidon over `[tip, l1_gas_felt, l2_gas_felt(0), l1_data_gas_felt(0)]` (4 elements).  
These produce **different hashes** for the same numeric gas values.

**Serialization round-trip confirms the collapse.** The `From<ValidResourceBounds> for protobuf::ResourceBounds` serializer always emits all three fields for both variants:

```rust
ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
    l1_gas: Some(l1_gas.into()),
    l2_gas: Some(value.get_l2_bounds().into()),          // zero
    l1_data_gas: Some(ResourceBounds::default().into()), // zero
},
``` [4](#0-3) 

So an `AllResources` transaction with zero l2/l1_data serializes identically to an `L1Gas` transaction in protobuf, and the deserializer cannot distinguish them — it always produces `L1Gas` for the zero-value case.

---

### Impact Explanation

**Matching impact: High — Transaction conversion or signature/hash logic binds the wrong hash.**

A user submits a post-0.13.3 `AllResources` transaction (e.g., via RPC) with `l2_gas = 0` and `l1_data_gas = 0`. The RPC path preserves `AllResources` (via `From<AllResourceBounds> for ValidResourceBounds` which always produces `AllResources`). The user signs hash H₁ computed over 4 elements. The RPC gateway accepts the transaction.

When the transaction is propagated to a peer via P2P (protobuf), the receiving node deserializes it as `L1Gas` and recomputes hash H₂ over 3 elements. H₁ ≠ H₂. The receiving node's signature verification fails and the transaction is rejected, or the node stores it under a different hash, breaking deduplication and block-hash consistency.

The `ValidResourceBounds::AllResources` → `ValidResourceBounds::L1Gas` collapse also changes `get_gas_vector_computation_mode()` from `All` to `NoL2Gas`, altering which resource bounds are enforced during execution. [5](#0-4) 

---

### Likelihood Explanation

Any post-0.13.3 transaction that sets `l2_gas.max_amount = 0`, `l2_gas.max_price_per_unit = 0`, `l1_data_gas.max_amount = 0`, `l1_data_gas.max_price_per_unit = 0` triggers the collapse. This is a valid and common configuration for transactions that only consume L1 gas (e.g., simple ETH transfers or transactions on networks where L2 gas is not yet priced). No special privilege is required; any user submitting such a transaction via the RPC gateway triggers the divergence.

---

### Recommendation

Replace the value-based variant selection in the protobuf deserializer with an explicit protocol-version tag or a dedicated boolean field in the protobuf schema that records whether the transaction was submitted as `AllResources` (post-0.13.3) or `L1Gas` (pre-0.13.3). Until the schema is updated, the deserializer should default to `AllResources` whenever `l1_data_gas` is present in the protobuf message (matching the JSON path's key-presence semantics), rather than collapsing to `L1Gas` on zero values:

```rust
// Proposed fix: presence of l1_data_gas field → AllResources, absence → L1Gas
Ok(if value.l1_data_gas.is_none() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

This aligns the protobuf path with the JSON deserialization path in `TryFrom<DeprecatedResourceBoundsMapping>`. [6](#0-5) 

---

### Proof of Concept

```
1. Construct a post-0.13.3 InvokeV3 transaction with:
     resource_bounds = AllResourceBounds {
         l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     }

2. Compute hash H1 using get_tip_resource_bounds_hash with AllResources:
     resource_felts = [l1_gas_felt, l2_gas_felt(0), l1_data_gas_felt(0)]  // 3 felts
     tip_hash = poseidon([tip, l1_gas_felt, l2_gas_felt, l1_data_gas_felt])
     H1 = poseidon([INVOKE, version, sender, tip_hash, ...])

3. Sign H1 and submit via RPC → accepted (AllResources path preserved).

4. Serialize to protobuf via From<ValidResourceBounds>:
     l1_gas = {1000, 1}, l2_gas = {0, 0}, l1_data_gas = {0, 0}  // all present

5. Deserialize via TryFrom<protobuf::ResourceBounds>:
     l1_data_gas.is_zero() && l2_gas.is_zero() == true
     → ValidResourceBounds::L1Gas({1000, 1})   // collapsed

6. Compute hash H2 using get_tip_resource_bounds_hash with L1Gas:
     resource_felts = [l1_gas_felt, l2_gas_felt(0)]  // only 2 felts
     tip_hash = poseidon([tip, l1_gas_felt, l2_gas_felt])
     H2 = poseidon([INVOKE, version, sender, tip_hash, ...])

7. H1 ≠ H2 — the P2P-receiving node rejects the transaction or stores it
   under a different hash, breaking cross-node consistency.
``` [7](#0-6) [8](#0-7)

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

**File:** crates/starknet_api/src/transaction/fields.rs (L416-421)
```rust
    pub fn get_gas_vector_computation_mode(&self) -> GasVectorComputationMode {
        match self {
            Self::AllResources(_) => GasVectorComputationMode::All,
            Self::L1Gas(_) => GasVectorComputationMode::NoL2Gas,
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
