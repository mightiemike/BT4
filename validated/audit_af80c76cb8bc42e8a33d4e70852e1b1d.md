### Title
`ValidResourceBounds` Protobuf Round-Trip Silently Collapses `AllResources` to `L1Gas`, Producing a Different Transaction Hash - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a value-based heuristic to reconstruct the variant: if `l2_gas` and `l1_data_gas` are both zero it emits `L1Gas`, otherwise `AllResources`. Because `get_tip_resource_bounds_hash` hashes a **different number of resource felts** for each variant (2 for `L1Gas`, 3 for `AllResources`), a transaction originally signed and admitted with `AllResources{l1_gas=X, l2_gas=0, l1_data_gas=0}` produces a different Poseidon hash after a protobuf round-trip. This is the sequencer analog of the AntePool gas-manipulation bug: just as insufficient gas silently causes `_checkTestNoRevert` to return `false`, the protobuf deserializer silently produces the wrong variant, causing the hash computation to return a wrong value.

---

### Finding Description

**Step 1 – Admission (RPC → Gateway)**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds` (never `ValidResourceBounds`). [1](#0-0) 

`InternalRpcInvokeTransactionV3` also stores `AllResourceBounds` and its `InvokeTransactionV3Trait` impl always wraps it in `ValidResourceBounds::AllResources(...)`. [2](#0-1) 

`InternalRpcTransactionWithoutTxHash::calculate_transaction_hash` therefore calls `get_invoke_transaction_v3_hash` with the `AllResources` variant, which includes the `L1_DATA_GAS` felt in the Poseidon preimage. [3](#0-2) 

**Step 2 – Hash preimage divergence**

`get_tip_resource_bounds_hash` branches on the variant:
- `L1Gas` → 2 resource felts (L1_GAS + L2_GAS)
- `AllResources` → 3 resource felts (L1_GAS + L2_GAS + L1_DATA_GAS) [4](#0-3) 

For `AllResources{l1_gas=X, l2_gas=0, l1_data_gas=0}` the hash preimage is `[tip, concat(L1_GAS,X), concat(L2_GAS,0), concat(L1_DATA,0)]` — four elements. For `L1Gas(X)` it is `[tip, concat(L1_GAS,X), concat(L2_GAS,0)]` — three elements. These yield different Poseidon digests.

**Step 3 – Lossy protobuf serialization**

`From<ValidResourceBounds> for protobuf::ResourceBounds` serializes both `L1Gas(X)` and `AllResources{l1_gas=X, l2_gas=0, l1_data_gas=0}` to the **identical** wire bytes: `{l1_gas: X, l2_gas: 0, l1_data_gas: 0}`. [5](#0-4) 

**Step 4 – Lossy protobuf deserialization**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` reconstructs the variant by checking whether the numeric values are zero:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← wrong variant
} else {
    ValidResourceBounds::AllResources(...)
})
``` [6](#0-5) 

A transaction originally admitted as `AllResources{l1_gas=X, l2_gas=0, l1_data_gas=0}` is reconstructed as `L1Gas(X)` after the round-trip. The hash recomputed from the deserialized form is therefore different from the hash stored in `InternalRpcTransaction.tx_hash`.

**Contrast: the RPC-transaction protobuf path is unaffected**

The separate `TryFrom<protobuf::ResourceBounds> for AllResourceBounds` converter (used for `RpcDeclareTransactionV3` and the consensus P2P path) always produces `AllResourceBounds` and is not lossy. [7](#0-6) 

The lossy converter is used specifically for `InvokeTransactionV3` (the starknet-api type), which is the type used in P2P state-sync block transmission.

---

### Impact Explanation

When a block containing an `InvokeTransactionV3` with `AllResources{l2_gas=0, l1_data_gas=0}` is transmitted over P2P state sync, the receiving peer deserializes the transaction with the wrong `ValidResourceBounds` variant. Any subsequent hash recomputation (e.g., `validate_transaction_hash`, signature verification, or receipt matching) produces a hash that does not match the committed `tx_hash`. The peer rejects the block as invalid, even though it is well-formed and was accepted by the sequencer. This matches the allowed impact: **"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."**

---

### Likelihood Explanation

The trigger is a valid, unprivileged user action: submitting an `InvokeV3` transaction with `AllResourceBounds` where `l2_gas.max_amount = 0` and `l1_data_gas.max_amount = 0`. The gateway accepts such transactions (no check prevents zero L2/data-gas bounds). The condition `l2_gas.is_zero() && l1_data_gas.is_zero()` is satisfied by any pre-0.13.3-style transaction that happens to carry zero L2 and data-gas bounds, which is a common pattern for transactions that only consume L1 gas.

---

### Recommendation

Replace the value-based heuristic in `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` with an explicit discriminant field in the protobuf schema (e.g., a `bounds_type` enum), or always deserialize as `AllResources` when all three fields are present in the wire message (since the serializer always emits all three fields for `AllResources`). The current approach is identical to the AntePool pattern: a silent fallback to a "simpler" representation when values happen to be zero, producing a semantically different result. [8](#0-7) 

---

### Proof of Concept

1. Submit an `RpcInvokeTransactionV3` with `resource_bounds = AllResourceBounds { l1_gas: {max_amount: 1000, max_price_per_unit: 1}, l2_gas: {max_amount: 0, max_price_per_unit: 0}, l1_data_gas: {max_amount: 0, max_price_per_unit: 0} }`.

2. The gateway computes `tx_hash_A` via `get_invoke_transaction_v3_hash` with `ValidResourceBounds::AllResources(...)` — the preimage includes the `L1_DATA_GAS` felt. [9](#0-8) 

3. The transaction is included in a block. Serialize the `InvokeTransactionV3` to protobuf via `From<ValidResourceBounds> for protobuf::ResourceBounds` — the wire bytes are `{l1_gas: 1000/1, l2_gas: 0/0, l1_data_gas: 0/0}`. [5](#0-4) 

4. A peer deserializes the protobuf: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas({max_amount: 1000, max_price_per_unit: 1})`. [8](#0-7) 

5. The peer recomputes `tx_hash_B` via `get_invoke_transaction_v3_hash` with `ValidResourceBounds::L1Gas(...)` — the preimage omits the `L1_DATA_GAS` felt. [10](#0-9) 

6. `tx_hash_A ≠ tx_hash_B`. The peer's hash validation fails; the block is rejected despite being valid.

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L124-140)
```rust
    pub fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
    ) -> Result<TransactionHash, StarknetApiError> {
        let transaction_version = &self.version();
        match self {
            InternalRpcTransactionWithoutTxHash::Declare(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
            InternalRpcTransactionWithoutTxHash::Invoke(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
            InternalRpcTransactionWithoutTxHash::DeployAccount(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
        }
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L550-566)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, SizeOf)]
pub struct RpcInvokeTransactionV3 {
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
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
    #[serde(default, skip_serializing_if = "Proof::is_empty")]
    pub proof: Proof,
}
```

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

**File:** crates/starknet_api/src/transaction_hash.rs (L370-404)
```rust
pub(crate) fn get_invoke_transaction_v3_hash<T: InvokeTransactionV3Trait>(
    transaction: &T,
    chain_id: &ChainId,
    transaction_version: &TransactionVersion,
) -> Result<TransactionHash, StarknetApiError> {
    let tip_resource_bounds_hash =
        get_tip_resource_bounds_hash(&transaction.resource_bounds(), transaction.tip())?;
    let paymaster_data_hash =
        HashChain::new().chain_iter(transaction.paymaster_data().0.iter()).get_poseidon_hash();
    let data_availability_mode = concat_data_availability_mode(
        transaction.nonce_data_availability_mode(),
        transaction.fee_data_availability_mode(),
    );
    let account_deployment_data_hash = HashChain::new()
        .chain_iter(transaction.account_deployment_data().0.iter())
        .get_poseidon_hash();
    let calldata_hash =
        HashChain::new().chain_iter(transaction.calldata().0.iter()).get_poseidon_hash();
    let mut hash_chain = HashChain::new()
        .chain(&INVOKE)
        .chain(&transaction_version.0)
        .chain(transaction.sender_address().0.key())
        .chain(&tip_resource_bounds_hash)
        .chain(&paymaster_data_hash)
        .chain(&Felt::try_from(chain_id)?)
        .chain(&transaction.nonce().0)
        .chain(&data_availability_mode)
        .chain(&account_deployment_data_hash)
        .chain(&calldata_hash);
    if !transaction.proof_facts().0.is_empty() {
        let proof_facts_hash =
            HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
        hash_chain = hash_chain.chain(&proof_facts_hash);
    }
    Ok(TransactionHash(hash_chain.get_poseidon_hash()))
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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L212-223)
```rust
impl TryFrom<protobuf::ResourceBounds> for AllResourceBounds {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        Ok(Self {
            l1_gas: value.l1_gas.ok_or(missing("ResourceBounds::l1_gas"))?.try_into()?,
            l2_gas: value.l2_gas.ok_or(missing("ResourceBounds::l2_gas"))?.try_into()?,
            l1_data_gas: value
                .l1_data_gas
                .ok_or(missing("ResourceBounds::l1_data_gas"))?
                .try_into()?,
        })
    }
```
