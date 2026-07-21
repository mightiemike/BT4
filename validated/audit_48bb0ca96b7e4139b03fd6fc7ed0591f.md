### Title
Protobuf `ValidResourceBounds` Round-Trip Silently Downgrades `AllResources` to `L1Gas` When l2_gas=0 and l1_data_gas=0, Causing P2P Consensus Transaction Deserialization Failure — (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserialization of `ValidResourceBounds` silently converts an `AllResources` variant into `L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero. A subsequent conversion step (`TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3`) unconditionally rejects any non-`AllResources` variant with a hard error. A user can craft a valid V3 invoke transaction with `AllResources { l2_gas: 0, l1_data_gas: 0 }`, have it accepted by the gateway, and then cause every receiving consensus peer to fail to deserialize the transaction, rejecting any block proposal that contains it.

### Finding Description

**Step 1 — Serialization (sender side)**

`From<ValidResourceBounds> for protobuf::ResourceBounds` always emits all three fields, even for `AllResources` with zero l2/l1_data bounds:

```rust
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),       // emitted as Some(zero)
        l1_data_gas: Some(l1_data_gas.into()), // emitted as Some(zero)
    }
``` [1](#0-0) 

**Step 2 — Deserialization (receiver side)**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` applies a value-based heuristic: if both `l2_gas` and `l1_data_gas` are zero, it returns `L1Gas` instead of `AllResources`:

```rust
let l1_data_gas = value.l1_data_gas.unwrap_or_default(); // Some(zero) → zero
// ...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← wrong variant
} else {
    ValidResourceBounds::AllResources(...)
})
``` [2](#0-1) 

**Step 3 — Conversion failure**

`TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` unconditionally rejects any non-`AllResources` variant:

```rust
resource_bounds: match value.resource_bounds {
    ValidResourceBounds::AllResources(bounds) => bounds,
    _ => {
        return Err(StarknetApiError::OutOfRange { string: "resource_bounds".to_string() });
    }
},
``` [3](#0-2) 

The P2P consensus path calls this conversion and maps the error to `DEPRECATED_RESOURCE_BOUNDS_ERROR`:

```rust
Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
``` [4](#0-3) 

**Step 4 — Hash domain divergence (secondary)**

Even if the deserialization were to succeed, `get_tip_resource_bounds_hash` produces a different hash for `AllResources` vs `L1Gas` when l2_gas=0 and l1_data_gas=0:

- `AllResources`: hashes `[tip, l1_gas_packed, l2_gas_packed(=0), l1_data_gas_packed(=0)]` — 4 elements
- `L1Gas`: hashes `[tip, l1_gas_packed, l2_gas_packed(=0)]` — 3 elements [5](#0-4) 

The `InternalRpcInvokeTransactionV3` struct always stores `resource_bounds: AllResourceBounds` (never `L1Gas`), so the gateway always computes the hash under the `AllResources` branch. A peer that somehow accepted the downgraded form would compute a different hash. [6](#0-5) 

### Impact Explanation

A user submits a V3 invoke transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` (both zero fields satisfy `is_zero()` only when `max_amount == 0 && max_price_per_unit == 0`). The gateway accepts it and computes a hash under `AllResources`. When the proposing sequencer node includes this transaction in a consensus block proposal and broadcasts it via P2P, every receiving peer fails to deserialize the transaction with `DEPRECATED_RESOURCE_BOUNDS_ERROR`, causing them to reject the block proposal. This breaks consensus liveness for any block containing such a transaction.

This matches: **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

### Likelihood Explanation

Any unprivileged user can submit a V3 invoke transaction with zero l2_gas and zero l1_data_gas through the public RPC endpoint. The gateway performs no check preventing `AllResourceBounds` with both fields fully zero. The trigger is a single crafted transaction.

### Recommendation

Replace the value-based heuristic in `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` with an explicit discriminator field in the protobuf schema (e.g., a `bounds_type` enum), or always deserialize to `AllResources` when all three fields are present in the wire message, regardless of their values. The `None`-vs-`Some(zero)` distinction must be preserved across the serialization boundary. The TODO comment (`// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2`) confirms this is a known transitional state that has not been resolved. [7](#0-6) 

### Proof of Concept

1. Construct an `RpcInvokeTransactionV3` with:
   ```
   resource_bounds = AllResourceBounds {
       l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
       l2_gas:      ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
       l1_data_gas: ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
   }
   ```
2. Submit via `starknet_addInvokeTransaction`. The gateway accepts it and stores hash H computed under `AllResources` (4-element poseidon input).
3. The proposing node serializes the transaction to `protobuf::ConsensusTransaction` → `protobuf::InvokeV3WithProof` → `protobuf::InvokeV3` with `resource_bounds = { l1_gas: Some(1000/1), l2_gas: Some(0/0), l1_data_gas: Some(0/0) }`.
4. A receiving peer deserializes: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas(...)`.
5. `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` returns `DEPRECATED_RESOURCE_BOUNDS_ERROR`.
6. The receiving peer rejects the block proposal. Consensus stalls for any block containing this transaction.

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L614-638)
```rust
/// An [RpcInvokeTransactionV3] that excludes the proof field (only keeps proof_facts).
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, SizeOf)]
pub struct InternalRpcInvokeTransactionV3 {
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub resource_bounds: AllResourceBounds,
    pub tip: Tip,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    pub proof_facts: ProofFacts,
}

impl InternalRpcInvokeTransactionV3 {
    pub fn version(&self) -> TransactionVersion {
        TransactionVersion::THREE
    }
}

impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L115-132)
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
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L187-211)
```rust
// An implementation of the SNIP: https://github.com/EvyatarO/SNIPs/blob/snip-8/SNIPS/snip-8.md
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
