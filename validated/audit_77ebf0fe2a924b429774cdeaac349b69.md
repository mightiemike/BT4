### Title
`AllResources`→`L1Gas` downgrade in protobuf round-trip produces divergent transaction hash and P2P rejection - (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion silently reclassifies a `ValidResourceBounds::AllResources { l2_gas=0, l1_data_gas=0 }` transaction as `ValidResourceBounds::L1Gas` during P2P deserialization. Because `get_tip_resource_bounds_hash` includes `l1_data_gas` in the hash preimage for `AllResources` but omits it for `L1Gas`, the transaction hash computed at the gateway diverges from the hash recomputed by any peer. The downstream `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` conversion then hard-rejects the reclassified transaction with `DEPRECATED_RESOURCE_BOUNDS_ERROR`, silently dropping a gateway-admitted transaction before sequencing.

### Finding Description

**Two variants, two hash preimages.**

`ValidResourceBounds` has two variants:

```
L1Gas(ResourceBounds)          // pre-0.13.3: only L1 gas
AllResources(AllResourceBounds) // 0.13.3+: L1 gas + L2 gas + L1 data gas
```

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` builds the fee-fields hash differently for each:

```rust
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
// For new V3 txs, need to also hash the data gas bounds.
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 felts
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 felts
    }
});
```

Even when `l2_gas=0` and `l1_data_gas=0`, the `AllResources` path hashes three felts while `L1Gas` hashes two. The resulting `tip_resource_bounds_hash` values are distinct, so the full transaction hashes diverge.

**The protobuf round-trip silently changes the variant.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` uses a zero-value heuristic to pick the variant:

```rust
let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // defaults to zero if absent
let l1_gas: ResourceBounds = l1_gas.try_into()?;
let l2_gas: ResourceBounds = l2_gas.try_into()?;
let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)                      // ← wrong variant
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

The serializer (`From<ValidResourceBounds> for protobuf::ResourceBounds`) faithfully encodes all three fields for `AllResources`:

```rust
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),
        l1_data_gas: Some(l1_data_gas.into()),   // present, but zero
    },
```

On the receiving peer, `l1_data_gas` is `Some(zero)`, `l1_data_gas.is_zero() && l2_gas.is_zero()` is `true`, and the variant is silently changed to `L1Gas`.

**The reclassified transaction is then hard-rejected.**

`TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` calls:

```rust
Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
```

which calls `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3`:

```rust
resource_bounds: match value.resource_bounds {
    ValidResourceBounds::AllResources(bounds) => bounds,
    _ => {
        return Err(StarknetApiError::OutOfRange {
            string: "resource_bounds".to_string(),
        });
    }
},
```

`L1Gas` is not `AllResources`, so the conversion fails and the transaction is dropped by the peer with `DEPRECATED_RESOURCE_BOUNDS_ERROR`.

**End-to-end divergence path.**

| Step | Location | Variant | Hash |
|------|----------|---------|------|
| Gateway admission | `convert_rpc_tx_to_internal` | `AllResources` | H₁ (3 felts) |
| P2P serialization | `From<ValidResourceBounds> for protobuf::ResourceBounds` | `AllResources` → protobuf | — |
| P2P deserialization | `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` | **`L1Gas`** | H₂ (2 felts) ≠ H₁ |
| Peer conversion | `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` | **FAIL** | — |

### Impact Explanation

A gateway-admitted `AllResources` invoke transaction with `l2_gas=0` and `l1_data_gas=0` is silently dropped by every peer that receives it over P2P. The transaction can never be sequenced. Additionally, if the conversion were to succeed (e.g., via a different code path), the blockifier would execute the transaction under hash H₂ while the account's signature covers H₁, causing `__validate__` to fail — binding the wrong hash to the executable payload.

This matches:
- **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**
- **High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

### Likelihood Explanation

The trigger condition — `AllResourceBounds { l2_gas=0, l1_data_gas=0 }` — is syntactically valid and accepted by the gateway. A user or SDK that submits a V3 invoke transaction with zero L2 and data-gas bounds (e.g., to avoid paying those fees, relying on L1 gas only) will hit this path. The gateway's `validate_tx_l2_gas_price_within_threshold` only rejects transactions where `l2_gas.max_price_per_unit` is below the threshold; a transaction with `l2_gas.max_amount=0` and a non-zero price passes that check but may still satisfy `is_zero()` if the method checks only `max_amount`. The condition is reachable without any privileged access.

### Recommendation

1. **Preserve variant identity in protobuf.** Add an explicit discriminant field to the protobuf `ResourceBounds` message (e.g., `bool is_all_resources`) so the deserializer does not rely on zero-value heuristics.

2. **Remove the zero-value heuristic.** The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion in `crates/apollo_protobuf/src/converters/transaction.rs` should not infer the variant from field values. The variant must be encoded explicitly.

3. **Add a round-trip invariant test.** Assert that `AllResources { l2_gas=0, l1_data_gas=0 }` survives a protobuf round-trip as `AllResources`, not `L1Gas`.

### Proof of Concept

```
1. Submit RPC invoke transaction:
   resource_bounds = AllResourceBounds {
       l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
       l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
       l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
   }

2. Gateway accepts it. Hash H₁ computed via get_tip_resource_bounds_hash with
   AllResources → 3 resource felts: [l1_gas_packed, l2_gas_packed=0, l1_data_gas_packed=0]

3. Transaction propagated via P2P as ConsensusTransaction::InvokeV3.
   Serialized protobuf ResourceBounds: { l1_gas=Some(X), l2_gas=Some(0), l1_data_gas=Some(0) }

4. Peer deserializes:
   TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
     l1_data_gas = Some(0).unwrap_or_default() = ResourceBounds { 0, 0 }
     l1_data_gas.is_zero() && l2_gas.is_zero() == true
     → returns ValidResourceBounds::L1Gas(l1_gas=X)   ← WRONG VARIANT

5. TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3:
     match value.resource_bounds {
         ValidResourceBounds::AllResources(_) => ...   // not matched
         _ => return Err(OutOfRange)                   // ← DEPRECATED_RESOURCE_BOUNDS_ERROR
     }

6. Transaction dropped by peer. Never sequenced.
   If execution were reached: H₂ (2 felts) ≠ H₁ (3 felts) → signature mismatch.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L115-131)
```rust
impl TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(mut value: protobuf::InvokeV3WithProof) -> Result<Self, Self::Error> {
        // Extract proof first, since `starknet_api::transaction::InvokeTransactionV3` does not
        // carry a `proof` field.
        let proof = Proof::from(std::mem::take(&mut value.proof));

        let snapi_invoke: InvokeTransactionV3 = value
            .invoke
            .ok_or(ProtobufConversionError::MissingField {
                field_description: "InvokeV3WithProof::invoke",
            })?
            .try_into()?;

        // This conversion can fail only if the resource_bounds are not AllResources.
        Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L586-611)
```rust
impl TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3 {
    type Error = StarknetApiError;

    fn try_from(value: InvokeTransactionV3) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => {
                    return Err(StarknetApiError::OutOfRange {
                        string: "resource_bounds".to_string(),
                    });
                }
            },
            signature: value.signature,
            nonce: value.nonce,
            tip: value.tip,
            paymaster_data: value.paymaster_data,
            nonce_data_availability_mode: value.nonce_data_availability_mode,
            fee_data_availability_mode: value.fee_data_availability_mode,
            sender_address: value.sender_address,
            calldata: value.calldata,
            account_deployment_data: value.account_deployment_data,
            proof_facts: value.proof_facts,
            proof: Proof::default(),
        })
    }
```
