### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Corrupting Transaction Hash Preimage and Fee Computation Mode — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation uses a value-based heuristic to distinguish between the pre-0.13.3 `L1Gas` variant and the post-0.13.3 `AllResources` variant. Any `AllResources` transaction whose `l2_gas` and `l1_data_gas` bounds are both zero is silently re-classified as `L1Gas` after protobuf deserialization. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` resource felt only for `AllResources`, the hash preimage changes, and the gas-vector computation mode switches from `All` to `NoL2Gas`, causing wrong fee estimation and simulation results on every node that receives the transaction via P2P sync.

---

### Finding Description

**Serialization side** (`From<ValidResourceBounds> for protobuf::ResourceBounds`, lines 471–489): both `L1Gas` and `AllResources{l2_gas=0, l1_data_gas=0}` produce an identical wire encoding — `l2_gas = Some(zero)`, `l1_data_gas = Some(zero)`.

**Deserialization side** (`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, lines 417–436):

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← wrong for AllResources with zero bounds
} else {
    ValidResourceBounds::AllResources(...)
})
```

The protobuf format cannot distinguish "field absent" (0.13.2 `L1Gas` transaction) from "field present but zero" (0.13.3+ `AllResources` transaction with zero L2/data-gas bounds). The heuristic therefore irreversibly converts the latter to `L1Gas`.

**Hash impact** (`get_tip_resource_bounds_hash`, `crates/starknet_api/src/transaction_hash.rs` lines 188–211):

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 felts total
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 felts total
    }
});
```

`L1_DATA_GAS` encodes as `b"L1_DATA"` in the upper 56 bits of the felt, so even with zero `max_amount` and `max_price_per_unit` the appended felt is non-zero. The Poseidon hash over 3 elements differs from the hash over 2 elements, so the hash computed from the deserialized `L1Gas` variant diverges from the hash originally committed to by the signer.

**Fee-mode impact**: `ValidResourceBounds::get_gas_vector_computation_mode()` returns `GasVectorComputationMode::All` for `AllResources` and `GasVectorComputationMode::NoL2Gas` for `L1Gas`. After the downgrade, the blockifier skips L2-gas and L1-data-gas bound checks entirely.

---

### Impact Explanation

A node that receives a qualifying transaction via P2P sync stores it with `ValidResourceBounds::L1Gas`. Every subsequent RPC call (`estimate_fee`, `simulate_transactions`, `trace_transaction`) re-executes the transaction under `NoL2Gas` mode, returning fee estimates that omit L2-gas and L1-data-gas costs — an authoritative-looking wrong value. Additionally, if any code path recomputes the transaction hash from the stored `Transaction` object (e.g., for block-hash commitment verification), it produces a hash that does not match the one the sender signed, breaking hash-binding of the executable payload.

---

### Likelihood Explanation

Any V3 transaction submitted with `AllResources` bounds where both `l2_gas.max_amount = 0` and `l1_data_gas.max_amount = 0` (and both `max_price_per_unit = 0`) triggers the bug. This is a structurally valid transaction — the testing helper `AllResourceBounds::create_for_testing()` produces exactly this shape. No special privilege is required; any user can submit such a transaction to the gateway.

---

### Recommendation

Replace the value-based heuristic with a structural discriminant. Options:

1. **Protobuf oneof**: add a `oneof bounds_type { L1GasBounds l1_gas_only = …; AllResourceBounds all_resources = …; }` field so the wire format carries the variant tag explicitly.
2. **Boolean flag**: add a `bool is_all_resources` field to `protobuf::ResourceBounds`; set it on serialization and read it on deserialization instead of inspecting values.
3. **Remove the TODO**: once 0.13.2 support is dropped, assert `l1_data_gas` is always `Some` and treat every message that carries all three fields as `AllResources`, regardless of whether the values are zero.

---

### Proof of Concept

```
1. Craft a V3 InvokeTransaction with:
     resource_bounds = AllResources {
         l1_gas:      { max_amount: N, max_price_per_unit: P },
         l2_gas:      { max_amount: 0, max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0, max_price_per_unit: 0 },
     }
   Sign it; the signer computes hash H1 via get_tip_resource_bounds_hash
   over [tip, l1_gas_felt, l2_gas_felt, l1_data_gas_felt] (3 resource felts).

2. Submit to gateway → accepted; stored internally as AllResources.

3. Block is produced; P2P sync server serialises the Transaction to protobuf:
     l1_gas      = Some(N, P)
     l2_gas      = Some(0, 0)
     l1_data_gas = Some(0, 0)   ← present but zero

4. Receiving node deserialises:
     l1_data_gas.unwrap_or_default() → zero
     l1_data_gas.is_zero() && l2_gas.is_zero() → true
     → ValidResourceBounds::L1Gas(l1_gas)   ← WRONG variant

5. Receiving node stores Transaction with L1Gas bounds.

6. RPC estimate_fee on receiving node:
     get_gas_vector_computation_mode() → NoL2Gas
     → L2-gas and L1-data-gas costs omitted from estimate
     → authoritative-looking wrong fee value returned to caller.

7. If hash is recomputed from stored Transaction:
     get_tip_resource_bounds_hash over [tip, l1_gas_felt, l2_gas_felt] (2 felts)
     → H2 ≠ H1  (l1_data_gas_felt = encode("L1_DATA" || 0 || 0) is non-zero)
     → hash binding broken.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/starknet_api/src/transaction/fields.rs (L363-421)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}

impl From<AllResourceBounds> for ValidResourceBounds {
    fn from(value: AllResourceBounds) -> Self {
        Self::AllResources(value)
    }
}

impl ValidResourceBounds {
    pub fn get_l1_bounds(&self) -> ResourceBounds {
        match self {
            Self::L1Gas(l1_bounds) => *l1_bounds,
            Self::AllResources(AllResourceBounds { l1_gas, .. }) => *l1_gas,
        }
    }

    pub fn get_l2_bounds(&self) -> ResourceBounds {
        match self {
            Self::L1Gas(_) => ResourceBounds::default(),
            Self::AllResources(AllResourceBounds { l2_gas, .. }) => *l2_gas,
        }
    }

    /// Returns the maximum possible fee that can be charged for the transaction.
    /// The computation is saturating, meaning that if the result is larger than the maximum
    /// possible fee, the maximum possible fee is returned.
    pub fn max_possible_fee(&self, tip: Tip) -> Fee {
        match self {
            ValidResourceBounds::L1Gas(l1_bounds) => {
                l1_bounds.max_amount.saturating_mul(l1_bounds.max_price_per_unit)
            }
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => l1_gas
                .max_amount
                .saturating_mul(l1_gas.max_price_per_unit)
                .saturating_add(
                    l2_gas
                        .max_amount
                        .saturating_mul(l2_gas.max_price_per_unit.saturating_add(tip.into())),
                )
                .saturating_add(
                    l1_data_gas.max_amount.saturating_mul(l1_data_gas.max_price_per_unit),
                ),
        }
    }

    pub fn get_gas_vector_computation_mode(&self) -> GasVectorComputationMode {
        match self {
            Self::AllResources(_) => GasVectorComputationMode::All,
            Self::L1Gas(_) => GasVectorComputationMode::NoL2Gas,
        }
    }
```
