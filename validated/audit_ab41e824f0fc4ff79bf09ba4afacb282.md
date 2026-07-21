### Title
`ValidResourceBounds` Protobuf Round-Trip Silently Mutates `AllResources`→`L1Gas`, Producing a Different Transaction Hash - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` silently downgrades `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` to `L1Gas(X)`. Because `get_tip_resource_bounds_hash` hashes a different number of resource-bound elements for each variant, the two representations produce **different transaction hashes** for identical economic intent. Any node that receives such a transaction over P2P, recomputes its hash from the deserialized form, and compares it against the canonical hash stored in the block will observe a mismatch — causing the transaction (and its containing block) to be rejected or stored under the wrong hash.

---

### Finding Description

**Step 1 — The hash diverges between the two variants.**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` builds the resource-bounds preimage differently depending on the enum arm:

```
L1Gas(X)          → poseidon(tip, pack(L1_GAS, X), pack(L2_GAS, 0))          // 3 elements
AllResources(X,0,0) → poseidon(tip, pack(L1_GAS, X), pack(L2_GAS, 0), pack(L1_DATA_GAS, 0))  // 4 elements
``` [1](#0-0) 

These are structurally different Poseidon inputs; the resulting felts are never equal.

**Step 2 — The protobuf round-trip silently converts the variant.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` applies the following decision:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant changed
} else {
    ValidResourceBounds::AllResources(...)
})
``` [2](#0-1) 

A `DeployAccountTransactionV3` or `InvokeTransactionV3` that was originally committed with `AllResources { l2_gas: 0, l1_data_gas: 0 }` is deserialized as `L1Gas` on the receiving side. The `ValidResourceBounds` type is the field type for both of these lower-level transaction structs. [3](#0-2) [4](#0-3) 

**Step 3 — The test layer acknowledges the invariant break.**

The consensus protobuf test explicitly works around this by injecting a non-zero `l2_gas.max_amount` before every round-trip assertion:

```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
    ...
    resource_bounds.l2_gas.max_amount = GasAmount(1);
    ...
}
``` [5](#0-4) 

The workaround is applied only in tests; no guard exists in the production serializer.

**Step 4 — The hash is recomputed from the deserialized form.**

`InternalRpcTransactionWithoutTxHash::calculate_transaction_hash` recomputes the hash from the deserialized transaction body before storing it as `InternalRpcTransaction.tx_hash`. If the body now carries `L1Gas` instead of `AllResources`, the recomputed hash diverges from the hash the sequencer originally committed. [6](#0-5) [7](#0-6) 

---

### Impact Explanation

A `DeployAccountTransactionV3` (or `InvokeTransactionV3`) submitted with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is valid and can be sequenced. After the block is committed, any peer that syncs the block over P2P will:

1. Deserialize the transaction's resource bounds as `L1Gas(X)` (variant mutation).
2. Recompute the transaction hash using the 3-element Poseidon preimage.
3. Obtain a hash that differs from the 4-element hash stored in the block receipt/header.
4. Either reject the block (sync stall) or store the transaction under the wrong hash (wrong receipt, wrong state key).

This matches: **High — Transaction conversion or signature/hash logic binds the wrong hash or executable payload.**

---

### Likelihood Explanation

Any user can craft a `DeployAccountTransactionV3` with zero `l2_gas` and `l1_data_gas` bounds (a valid configuration for pre-0.13.3-style transactions submitted through the new API). The trigger requires no privilege and no special network position — only submitting a transaction with all-zero secondary resource bounds.

---

### Recommendation

Remove the variant-collapsing heuristic from the protobuf deserializer. When all three resource-bound fields are present in the wire message, always reconstruct `AllResources`, regardless of whether the secondary bounds are zero:

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let l1_gas = ...;
        let l2_gas = ...;
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        // Always AllResources when all three fields are present.
        Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
    }
}
```

The `L1Gas` variant should only be constructed when the wire message genuinely omits the secondary fields (i.e., for legacy 0.13.2 messages that never included them). The existing `TODO(Shahak)` comment already anticipates removing the `unwrap_or_default` fallback once 0.13.2 support is dropped; the variant-collapse logic should be removed at the same time. [8](#0-7) 

---

### Proof of Concept

```
1. Craft a DeployAccountTransactionV3 with:
       resource_bounds = AllResources { l1_gas: {max_amount:1, max_price:1},
                                        l2_gas:  {max_amount:0, max_price:0},
                                        l1_data_gas: {max_amount:0, max_price:0} }

2. Submit via RPC → gateway computes hash H_all using the 4-element Poseidon preimage
   (tip || L1_GAS_packed || L2_GAS_packed(0) || L1_DATA_GAS_packed(0)).

3. Transaction is included in block B; receipt stores H_all.

4. Peer node syncs block B over P2P:
   - Protobuf message carries l2_gas=0, l1_data_gas=0.
   - TryFrom<protobuf::ResourceBounds> for ValidResourceBounds fires the
     `l1_data_gas.is_zero() && l2_gas.is_zero()` branch → returns L1Gas(l1_gas).

5. Peer recomputes hash H_l1 using the 3-element Poseidon preimage
   (tip || L1_GAS_packed || L2_GAS_packed(0)).

6. H_all ≠ H_l1 → validate_transaction_hash returns false →
   block rejected / transaction stored under wrong hash.
``` [9](#0-8) [10](#0-9)

### Citations

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

**File:** crates/starknet_api/src/transaction.rs (L514-522)
```rust
impl TransactionHasher for DeployAccountTransactionV3 {
    fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
        transaction_version: &TransactionVersion,
    ) -> Result<TransactionHash, StarknetApiError> {
        get_deploy_account_transaction_v3_hash(self, chain_id, transaction_version)
    }
}
```

**File:** crates/starknet_api/src/transaction.rs (L663-678)
```rust
/// An invoke V3 transaction.
#[derive(Debug, Clone, Eq, PartialEq, Hash, Deserialize, Serialize, PartialOrd, Ord)]
pub struct InvokeTransactionV3 {
    pub resource_bounds: ValidResourceBounds,
    pub tip: Tip,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
}
```

**File:** crates/apollo_protobuf/src/converters/consensus_test.rs (L26-48)
```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
    let transaction = &mut transactions[0];
    match transaction {
        ConsensusTransaction::RpcTransaction(rpc_transaction) => match rpc_transaction {
            RpcTransaction::Declare(RpcDeclareTransaction::V3(RpcDeclareTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::Invoke(RpcInvokeTransaction::V3(RpcInvokeTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(
                RpcDeployAccountTransactionV3 { resource_bounds, .. },
            )) => {
                resource_bounds.l2_gas.max_amount = GasAmount(1);
            }
        },
        ConsensusTransaction::L1Handler(_) => {}
    }
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L124-141)
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
}
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L334-392)
```rust
    async fn convert_rpc_tx_to_internal(
        &self,
        tx: RpcTransaction,
    ) -> TransactionConverterResult<(InternalRpcTransaction, Option<(ProofFacts, Proof)>)> {
        let (tx_without_hash, proof_data) = match tx {
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => {
                let proof_data = if tx.proof_facts.is_empty() {
                    None
                } else {
                    Some((tx.proof_facts.clone(), tx.proof.clone()))
                };
                (InternalRpcTransactionWithoutTxHash::Invoke(tx.into()), proof_data)
            }
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => {
                let ClassHashes { class_hash, executable_class_hash_v2 } =
                // TODO(Dori): Make this async and spawn a task to compile and add it to the class manager.
                    self.class_manager_client.add_class(tx.contract_class).await?;
                // TODO(Aviv): Ensure that we do not want to
                // allow declare with compiled class hash v1.
                if tx.compiled_class_hash != executable_class_hash_v2 {
                    return Err(TransactionConverterError::ValidateCompiledClassHashError(
                        ValidateCompiledClassHashError::CompiledClassHashMismatch {
                            computed_class_hash: executable_class_hash_v2,
                            supplied_class_hash: tx.compiled_class_hash,
                        },
                    ));
                }
                (
                    InternalRpcTransactionWithoutTxHash::Declare(InternalRpcDeclareTransactionV3 {
                        sender_address: tx.sender_address,
                        compiled_class_hash: tx.compiled_class_hash,
                        signature: tx.signature,
                        nonce: tx.nonce,
                        class_hash,
                        resource_bounds: tx.resource_bounds,
                        tip: tx.tip,
                        paymaster_data: tx.paymaster_data,
                        account_deployment_data: tx.account_deployment_data,
                        nonce_data_availability_mode: tx.nonce_data_availability_mode,
                        fee_data_availability_mode: tx.fee_data_availability_mode,
                    }),
                    None,
                )
            }
            RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(tx)) => {
                let contract_address = tx.calculate_contract_address()?;
                (
                    InternalRpcTransactionWithoutTxHash::DeployAccount(
                        InternalRpcDeployAccountTransaction {
                            tx: RpcDeployAccountTransaction::V3(tx),
                            contract_address,
                        },
                    ),
                    None,
                )
            }
        };
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```
