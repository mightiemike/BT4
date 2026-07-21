### Title
`ValidResourceBounds::AllResources` with zero L2/data-gas silently mutates to `ValidResourceBounds::L1Gas` across protobuf round-trip, producing a divergent transaction hash — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` collapses any `AllResources` variant whose `l2_gas` and `l1_data_gas` fields are both zero into the `L1Gas` variant. Because `get_tip_resource_bounds_hash` produces a structurally different Poseidon preimage for the two variants (three elements for `L1Gas`, four elements for `AllResources`), any V3 transaction that was originally hashed under `AllResources` semantics will produce a different transaction hash after a protobuf round-trip. This is a canonicalization invariant violation: the same logical transaction binds to two different hashes depending on which side of the wire boundary it is observed from.

---

### Finding Description

**Serialization side** — `From<ValidResourceBounds> for protobuf::ResourceBounds`:

When the variant is `L1Gas`, both `l2_gas` and `l1_data_gas` are emitted as `ResourceBounds::default()` (all-zero). [1](#0-0) 

**Deserialization side** — `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`:

The decoder inspects the wire values and, if both `l1_data_gas` and `l2_gas` are zero, reconstructs `L1Gas` — regardless of whether the sender originally held `AllResources`. [2](#0-1) 

This means `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` and `L1Gas(X)` are **indistinguishable** in the protobuf wire format. Any `AllResources` transaction with zero L2/data-gas is silently promoted to `L1Gas` on receipt.

**Hash divergence** — `get_tip_resource_bounds_hash`:

The function branches on the variant tag, not on the numeric values:

- `L1Gas` → preimage = `[tip, L1_GAS_concat, L2_GAS_concat]` (3 elements)
- `AllResources` → preimage = `[tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat]` (4 elements) [3](#0-2) 

Even when `l1_data_gas` is all-zero, appending `get_concat_resource(&zero_bounds, L1_DATA_GAS)` changes the Poseidon digest. The two variants therefore produce **different transaction hashes for identical numeric field values**.

**Affected transaction types**: All V3 transactions (`InvokeTransactionV3`, `DeclareTransactionV3`, `DeployAccountTransactionV3`) that carry `ValidResourceBounds` go through this converter on the P2P sync wire path. [4](#0-3) 

**Triggering input**: A V3 transaction submitted with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`. This is the canonical shape of pre-0.13.3 V3 transactions (the comment in `get_tip_resource_bounds_hash` explicitly notes "Old V3 txs always have L2 gas bounds of zero"). The gateway accepts `AllResourceBounds` directly from users; the conversion to `ValidResourceBounds::AllResources` happens inside the sequencer. [5](#0-4) 

---

### Impact Explanation

Any component that receives a `Transaction` over the protobuf P2P wire and then recomputes the transaction hash (e.g., for block-commitment verification via `validate_transaction_hash`, or for signature verification) will derive a hash that differs from the hash computed by the originating node. Concretely:

1. **Wrong transaction hash bound to the executable payload** — the deserialized `InvokeTransactionV3` carries `L1Gas` but the signature was produced over the `AllResources` hash. Signature verification against the recomputed hash fails, or the wrong hash is stored and propagated.

2. **Transaction commitment mismatch** — the block's `transaction_commitment` field is computed from the original hashes; a syncing node that recomputes hashes from the deserialized transactions will derive a different commitment, causing block validation to fail or silently diverge.

This matches the allowed impact: **"High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."**

---

### Likelihood Explanation

- Pre-0.13.3 V3 transactions (l2_gas = 0, l1_data_gas = 0) are the dominant historical transaction shape and continue to be valid inputs.
- No special privilege is required; any user can submit a V3 transaction with zero L2/data-gas bounds.
- The bug fires on every protobuf round-trip for such transactions — it is not probabilistic.
- The existing TODO comment `// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2` confirms the zero-data-gas path is intentionally kept alive. [6](#0-5) 

---

### Recommendation

Add an explicit discriminator to the protobuf `ResourceBounds` message (e.g., a boolean `is_all_resources` flag, or a separate oneof), so the deserializer can reconstruct the correct variant without relying on numeric zero-ness. Alternatively, always deserialize to `AllResources` when all three resource fields are present on the wire, and only fall back to `L1Gas` when `l1_data_gas` is absent (matching the pre-0.13.3 wire format where the field did not exist). The current heuristic `l1_data_gas.is_zero() && l2_gas.is_zero()` conflates two semantically distinct states.

---

### Proof of Concept

```
// Originating node:
let bounds = ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas:      ResourceBounds { max_amount: GasAmount(100), max_price_per_unit: GasPrice(1) },
    l2_gas:      ResourceBounds::default(),   // zero
    l1_data_gas: ResourceBounds::default(),   // zero
});
// hash_A = get_tip_resource_bounds_hash(&bounds, &tip)
// preimage length = 4  (includes L1_DATA_GAS_concat(zero))

// After protobuf round-trip:
let proto: protobuf::ResourceBounds = bounds.into();
// proto.l2_gas      = ResourceLimits { max_amount: 0, max_price_per_unit: 0 }
// proto.l1_data_gas = ResourceLimits { max_amount: 0, max_price_per_unit: 0 }

let recovered = ValidResourceBounds::try_from(proto).unwrap();
// recovered == ValidResourceBounds::L1Gas(ResourceBounds { max_amount: 100, max_price_per_unit: 1 })
// hash_B = get_tip_resource_bounds_hash(&recovered, &tip)
// preimage length = 3  (L1_DATA_GAS_concat omitted)

assert_ne!(hash_A, hash_B);  // diverges — same numeric values, different Poseidon digest
```

The divergence is confirmed by `get_tip_resource_bounds_hash`'s branch: `ValidResourceBounds::L1Gas(_) => vec![]` vs `ValidResourceBounds::AllResources(all_resources) => vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]`. [7](#0-6)

### Citations

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L663-699)
```rust
impl From<InvokeTransactionV3> for protobuf::InvokeV3 {
    fn from(value: InvokeTransactionV3) -> Self {
        Self {
            resource_bounds: Some(protobuf::ResourceBounds::from(value.resource_bounds)),
            tip: value.tip.0,
            signature: Some(protobuf::AccountSignature {
                parts: value.signature.0.iter().map(|signature| (*signature).into()).collect(),
            }),
            nonce: Some(value.nonce.0.into()),
            sender: Some(value.sender_address.into()),
            calldata: value.calldata.0.iter().map(|calldata| (*calldata).into()).collect(),
            nonce_data_availability_mode: volition_domain_to_enum_int(
                value.nonce_data_availability_mode,
            ),
            fee_data_availability_mode: volition_domain_to_enum_int(
                value.fee_data_availability_mode,
            ),
            paymaster_data: value
                .paymaster_data
                .0
                .iter()
                .map(|paymaster_data| (*paymaster_data).into())
                .collect(),
            account_deployment_data: value
                .account_deployment_data
                .0
                .iter()
                .map(|account_deployment_data| (*account_deployment_data).into())
                .collect(),
            proof_facts: value
                .proof_facts
                .0
                .iter()
                .map(|proof_fact| (*proof_fact).into())
                .collect(),
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L568-583)
```rust
impl From<RpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: RpcInvokeTransactionV3) -> Self {
        Self {
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
            signature: tx.signature,
            nonce: tx.nonce,
            sender_address: tx.sender_address,
            calldata: tx.calldata,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
            proof_facts: tx.proof_facts,
        }
    }
```
