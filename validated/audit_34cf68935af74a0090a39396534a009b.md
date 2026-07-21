### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash - (File: crates/apollo_protobuf/src/converters/transaction.rs)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation silently converts an `AllResources` variant (with zero `l2_gas` and zero `l1_data_gas`) into a `L1Gas` variant. Because `get_tip_resource_bounds_hash` hashes a different number of elements for each variant, the same transaction produces two distinct Poseidon hashes depending on whether it was processed through the gateway/RPC path or the P2P protobuf sync path.

### Finding Description

`ValidResourceBounds` has two variants:

- `L1Gas(ResourceBounds)` — pre-0.13.3, hashes **two** resource elements (L1\_GAS, L2\_GAS)
- `AllResources(AllResourceBounds)` — post-0.13.3, hashes **three** resource elements (L1\_GAS, L2\_GAS, L1\_DATA\_GAS) [1](#0-0) 

`get_tip_resource_bounds_hash` branches on the variant to decide how many elements to include: [2](#0-1) 

For `L1Gas`, only two resource felts are hashed; for `AllResources`, a third (`L1_DATA_GAS`) is appended. Because Poseidon is length-sensitive, `hash(tip, L1, L2)` ≠ `hash(tip, L1, L2, L1_DATA=0)`.

The gateway/RPC path always stores resource bounds as `AllResourceBounds` (never `ValidResourceBounds`), so the hash is always computed with three resource elements: [3](#0-2) [4](#0-3) 

However, the protobuf deserializer for `ValidResourceBounds` (used in the P2P block-sync path) applies a heuristic: if both `l2_gas` and `l1_data_gas` are zero, it silently returns `L1Gas` instead of `AllResources`: [5](#0-4) 

The comment on line 426 even acknowledges this is a temporary compatibility workaround for 0.13.2.

### Impact Explanation

A V3 invoke transaction submitted with `AllResources` where `l2_gas = 0` and `l1_data_gas = 0` (a valid and common configuration — only L1 gas is consumed) receives hash **H₁** at the gateway (computed with three resource elements). When that transaction is serialized to protobuf and deserialized by a syncing peer, the resource bounds are reconstructed as `L1Gas`, and any hash recomputation or validation produces hash **H₂** (two resource elements). H₁ ≠ H₂.

This breaks the hash-domain invariant: the same signed transaction payload produces two different canonical hashes depending on which code path processes it. Concretely:

- A syncing node that recomputes or validates the transaction hash from the deserialized `InvokeTransactionV3` will reject a valid block, or store the transaction under the wrong hash.
- The RPC layer serving transaction receipts or traces after sync will return an authoritative-looking wrong transaction hash.

This matches the **High** impact scope: *"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."*

### Likelihood Explanation

Any V3 invoke transaction that specifies only L1 gas (setting `l2_gas = 0` and `l1_data_gas = 0`) triggers the downgrade. This is a normal configuration for transactions that do not use L2 gas or data-gas resources. The trigger is unprivileged and requires no special permissions — any user submitting a standard V3 transaction can hit this path.

### Recommendation

Remove the heuristic downgrade in the protobuf deserializer. Always deserialize into `AllResources` when all three resource fields are present (even if zero), preserving the variant that was originally signed:

```rust
// In TryFrom<protobuf::ResourceBounds> for ValidResourceBounds
// Replace the heuristic branch:
Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
// Remove the L1Gas branch entirely for new transactions.
```

The `L1Gas` variant should only be produced when deserializing genuinely old (pre-0.13.3) transactions that were originally signed without a data-gas element. A version field or explicit protocol-version gate should control this, not a zero-value heuristic.

### Proof of Concept

1. Submit a V3 invoke transaction with `resource_bounds = AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`.
2. The gateway calls `get_invoke_transaction_v3_hash` via `InternalRpcInvokeTransactionV3::calculate_transaction_hash`, which calls `get_tip_resource_bounds_hash` with `AllResources` → hash includes `L1_DATA_GAS` element → produces **H₁**.
3. Serialize the transaction to protobuf and deserialize via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. Because `l2_gas.is_zero() && l1_data_gas.is_zero()`, the result is `ValidResourceBounds::L1Gas(l1_gas)`.
4. Recompute the hash from the deserialized `InvokeTransactionV3` → `get_tip_resource_bounds_hash` with `L1Gas` → hash excludes `L1_DATA_GAS` element → produces **H₂**.
5. Assert `H₁ != H₂`. The Poseidon hash of `[tip, L1_GAS_packed, L2_GAS_packed(0)]` differs from `[tip, L1_GAS_packed, L2_GAS_packed(0), L1_DATA_GAS_packed(0)]` by construction. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
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

**File:** crates/starknet_api/src/transaction_hash.rs (L370-405)
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
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L616-628)
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
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
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
