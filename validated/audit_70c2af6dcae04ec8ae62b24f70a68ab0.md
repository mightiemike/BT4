### Title
`ValidResourceBounds` Variant Divergence Between RPC Ingestion and P2P Protobuf Deserialization Produces Inconsistent Transaction Hashes — (File: `crates/starknet_api/src/rpc_transaction.rs`, `crates/apollo_protobuf/src/converters/transaction.rs`, `crates/starknet_api/src/transaction_hash.rs`)

### Summary

The RPC ingestion path always stores and hashes `AllResources`-variant resource bounds, while the P2P protobuf deserialization path silently downgrades the same wire data to the `L1Gas` variant when `l2_gas` and `l1_data_gas` are both zero. Because `get_tip_resource_bounds_hash` produces structurally different Poseidon preimages for the two variants (two vs. three resource elements), the canonical transaction hash computed at the gateway differs from the hash any peer recomputes after receiving the transaction over P2P. This is the direct sequencer analog of the Atlas/Sorter `getBidValue()` inconsistency: one code path uses a canonical accessor (`AllResources`) while another bypasses it (`L1Gas`), and the two values diverge for a reachable input.

### Finding Description

**Divergent representation in the two paths**

`InternalRpcInvokeTransactionV3` and `InternalRpcDeclareTransactionV3` store resource bounds as the concrete `AllResourceBounds` type and unconditionally wrap it in `ValidResourceBounds::AllResources` when implementing the hash trait:

```rust
// crates/starknet_api/src/rpc_transaction.rs
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)  // always AllResources
    }
``` [1](#0-0) 

The same pattern holds for `InternalRpcDeclareTransactionV3`: [2](#0-1) 

The protobuf deserializer, however, applies a heuristic: if both `l2_gas` and `l1_data_gas` are zero it produces `L1Gas`, not `AllResources`:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [3](#0-2) 

**Why the hashes differ**

`get_tip_resource_bounds_hash` builds a Poseidon preimage that includes a different number of elements depending on the variant:

```rust
// crates/starknet_api/src/transaction_hash.rs
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 elements total
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 elements total
    }
});
``` [4](#0-3) 

`AllResources` with `l1_data_gas = 0` still contributes a third element (`L1_DATA_GAS || 0 || 0`) to the hash, while `L1Gas` contributes only two elements. The resulting Poseidon digests are therefore different even when the numeric field values are identical.

**Concrete divergence path**

1. A user submits an `InvokeV3` or `DeclareV3` transaction via RPC with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`.
2. The gateway stores it as `InternalRpcInvokeTransactionV3` (field type `AllResourceBounds`) and computes hash **H₁** via `get_invoke_transaction_v3_hash` → `get_tip_resource_bounds_hash` with `AllResources` (3-element preimage). [5](#0-4) 

3. The transaction is propagated over P2P. The serializer emits `l2_gas = 0`, `l1_data_gas = 0` on the wire. [6](#0-5) 

4. The receiving node deserializes to `ValidResourceBounds::L1Gas(l1_gas)` and recomputes hash **H₂** with a 2-element preimage.
5. **H₁ ≠ H₂**. Any hash-based deduplication, mempool admission check, or block-hash verification on the receiving node will treat the transaction as invalid or as a different transaction.

### Impact Explanation

The transaction hash is the canonical identifier used for admission, deduplication, signature binding, and block commitment. A transaction accepted by the gateway with hash H₁ arrives at every P2P peer with a locally recomputed hash H₂ ≠ H₁. This matches the allowed impact scope:

> **High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

A valid, signed transaction is effectively unroutable across the P2P layer: the gateway accepts it under one hash while every peer rejects or re-identifies it under a different hash. An adversary who knows this can craft transactions that are permanently stuck in the gateway mempool and never sequenced, or can cause hash-based state divergence between nodes.

### Likelihood Explanation

The trigger condition — `AllResources` with `l2_gas = 0` and `l1_data_gas = 0` — is the natural state for any user who sets only an L1 gas bound but uses the newer `AllResources` wire format (which is the only format accepted by the current RPC types `RpcInvokeTransactionV3` / `RpcDeclareTransactionV3`). No special privilege is required; any unprivileged user submitting a standard V3 transaction with only L1 gas bounds set triggers the divergence.

### Recommendation

The protobuf deserializer's downgrade heuristic must be removed or made version-aware. The canonical rule should be: if the wire message was produced from an `AllResources` transaction (i.e., the sender is running a post-0.13.3 node), always deserialize as `AllResources`, regardless of whether `l2_gas` and `l1_data_gas` are zero. One concrete fix is to include an explicit discriminator field in the protobuf `ResourceBounds` message that records which `ValidResourceBounds` variant was serialized, and use that field — not a zero-value heuristic — to reconstruct the variant on deserialization.

Alternatively, align the RPC ingestion path with the protobuf path: if `l2_gas = 0` and `l1_data_gas = 0`, store and hash the transaction as `L1Gas` from the start, so both paths produce the same variant and therefore the same hash.

### Proof of Concept

```
1. Submit via RPC:
   InvokeV3 {
     resource_bounds: AllResourceBounds {
       l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
       l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
       l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     },
     tip: 0,
     ...
   }

2. Gateway path (InternalRpcInvokeTransactionV3):
   resource_bounds() → ValidResourceBounds::AllResources(...)
   get_tip_resource_bounds_hash preimage:
     poseidon(0, concat(L1_GAS,1000,1), concat(L2_GAS,0,0), concat(L1_DATA_GAS,0,0))
   → H₁

3. P2P protobuf round-trip:
   serialize: l1_gas=1000/1, l2_gas=0/0, l1_data_gas=0/0
   deserialize: l1_data_gas.is_zero() && l2_gas.is_zero() → L1Gas(l1_gas)
   get_tip_resource_bounds_hash preimage:
     poseidon(0, concat(L1_GAS,1000,1), concat(L2_GAS,0,0))
   → H₂

4. H₁ ≠ H₂  (different Poseidon inputs: 3 vs 2 resource elements)
``` [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L408-411)
```rust
impl DeclareTransactionV3Trait for InternalRpcDeclareTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L616-639)
```rust
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
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L669-676)
```rust
impl TransactionHasher for InternalRpcInvokeTransactionV3 {
    fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
        transaction_version: &TransactionVersion,
    ) -> Result<TransactionHash, StarknetApiError> {
        get_invoke_transaction_v3_hash(self, chain_id, transaction_version)
    }
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
