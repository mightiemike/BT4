### Title
`unwrap_or_default()` on `l1_data_gas` in protobuf `ValidResourceBounds` conversion silently produces wrong resource bounds, causing hash divergence and wrong re-execution results - (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation in `crates/apollo_protobuf/src/converters/transaction.rs` silently substitutes `ResourceBounds::default()` (all-zero) when the `l1_data_gas` field is absent from the protobuf message. This is asymmetric with the `AllResourceBounds` converter in `crates/apollo_protobuf/src/converters/rpc_transaction.rs`, which hard-errors on a missing `l1_data_gas`. A malicious P2P peer can send a state-sync or consensus protobuf for an `AllResources` V3 transaction with `l1_data_gas = None` but `l2_gas > 0`. The receiving node reconstructs `ValidResourceBounds::AllResources` with `l1_data_gas = 0`, which is a different value than what the user signed. Because `get_tip_resource_bounds_hash` includes `l1_data_gas` in the hash preimage for `AllResources` transactions, the recomputed hash diverges from the user-signed hash. In the consensus path this binds the wrong hash to the executable payload; in the state-sync path the wrong bounds are persisted to the DB and cause divergent re-execution results during proof generation.

---

### Finding Description

**Root cause — asymmetric protobuf converters**

`crates/apollo_protobuf/src/converters/transaction.rs` lines 417–436:

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let Some(l1_gas) = value.l1_gas else { return Err(missing("ResourceBounds::l1_gas")); };
        let Some(l2_gas) = value.l2_gas else { return Err(missing("ResourceBounds::l2_gas")); };
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // ← silent zero-fill
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
}
``` [1](#0-0) 

The sibling converter used for the mempool P2P path hard-errors instead:

```rust
impl TryFrom<protobuf::ResourceBounds> for AllResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        Ok(Self {
            l1_gas:      value.l1_gas.ok_or(missing("ResourceBounds::l1_gas"))?.try_into()?,
            l2_gas:      value.l2_gas.ok_or(missing("ResourceBounds::l2_gas"))?.try_into()?,
            l1_data_gas: value.l1_data_gas
                             .ok_or(missing("ResourceBounds::l1_data_gas"))?.try_into()?,
        })
    }
}
``` [2](#0-1) 

**Hash preimage includes `l1_data_gas` for `AllResources`**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` conditionally appends the `l1_data_gas` felt only when the variant is `AllResources`:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
``` [3](#0-2) 

Therefore `AllResources{l1_data_gas=X}` and `AllResources{l1_data_gas=0}` produce different hashes.

**Consensus path — hash bound to wrong payload**

The consensus protobuf conversion chain for an `InvokeV3` is:

1. `TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction` → calls `txn.try_into()` for `InvokeV3WithProof`
2. `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` → calls `InvokeTransactionV3::try_from(protobuf::InvokeV3)` which uses the lenient `unwrap_or_default()` converter
3. `convert_consensus_tx_to_internal_consensus_tx` → `convert_rpc_tx_to_internal` → **recomputes the hash** from the reconstructed transaction data [4](#0-3) [5](#0-4) 

If a malicious proposer sends `l1_data_gas = None` with `l2_gas > 0`, every validator reconstructs `AllResources{l1_data_gas=0}` and recomputes hash H2 ≠ H1 (the user-signed hash). The block is committed with H2. During execution the account's `__validate__` entry point checks the signature against H2 and fails, causing the transaction to revert — a wrong execution result for an accepted input.

**State-sync path — wrong bounds persisted to DB**

`TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` (used by `TransactionInBlock` state-sync deserialization) stores the transaction with `l1_data_gas = 0`. The transaction hash is taken verbatim from the protobuf (not recomputed), so block-hash verification passes. However, the wrong `resource_bounds` are written to the MDBX storage. When the SNOS re-executes the transaction for proof generation, the fee check enforces `l1_data_gas_used ≤ 0`, causing the transaction to revert in re-execution even though it succeeded in the original execution. This produces a divergent receipt/state and an invalid proof. [6](#0-5) 

---

### Impact Explanation

**Consensus path (malicious proposer):** The wrong hash H2 is committed to the block. The account's `__validate__` fails because the user's signature covers H1. The transaction is included as reverted. This is a wrong revert result from execution logic for an accepted input — **Critical** ("Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input").

**State-sync path (malicious P2P peer):** Wrong `resource_bounds` are persisted. SNOS re-execution uses `l1_data_gas_max = 0`, causing the transaction to revert when it originally succeeded. The generated proof attests to a different execution than what was committed — **Critical** (same category).

**Consensus path secondary:** The hash recomputed from the wrong payload binds the wrong hash to the executable transaction — **High** ("Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload").

---

### Likelihood Explanation

- **State-sync path**: Any P2P peer can send a malformed `TransactionInBlock` protobuf. No stake or special role is required. The attack is triggered by a single malformed message for any `AllResources` V3 transaction with non-zero `l1_data_gas` and `l2_gas`.
- **Consensus path**: Requires being the block proposer for a round, which is a validator role. Likelihood is lower but the impact is higher.
- The `unwrap_or_default()` is explicitly marked with a TODO acknowledging it is a temporary backward-compatibility measure, confirming the field is expected to be present for current transactions.

---

### Recommendation

Replace `unwrap_or_default()` with an explicit error for any transaction whose `l2_gas` is non-zero (i.e., any post-0.13.2 `AllResources` transaction):

```rust
// In crates/apollo_protobuf/src/converters/transaction.rs
let l1_data_gas = if l2_gas_raw.is_zero() {
    // Pre-0.13.3 backward compat: l1_data_gas may be absent.
    value.l1_data_gas.unwrap_or_default()
} else {
    // Post-0.13.3: l1_data_gas must be present.
    value.l1_data_gas.ok_or(missing("ResourceBounds::l1_data_gas"))?
};
```

This preserves backward compatibility for 0.13.2 transactions (where `l2_gas = 0`) while rejecting malformed post-0.13.3 transactions.

---

### Proof of Concept

**State-sync hash divergence (unprivileged)**

1. Honest node A has an `InvokeV3` transaction with `AllResources{l1_gas=G1, l2_gas=G2>0, l1_data_gas=D>0}`. Hash H1 is computed including `D`.
2. Malicious peer B sends a `TransactionInBlock` protobuf for this transaction with `resource_bounds.l1_data_gas = None` (field omitted) and `resource_bounds.l2_gas = G2 > 0`.
3. Node A's `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` hits `unwrap_or_default()`, producing `AllResources{l1_gas=G1, l2_gas=G2, l1_data_gas=0}`.
4. The transaction hash H1 is stored verbatim from the protobuf (block-hash check passes).
5. The wrong `resource_bounds` (with `l1_data_gas=0`) are written to MDBX.
6. SNOS re-execution loads the stored transaction. Fee check enforces `l1_data_gas_used ≤ 0`. The transaction uses `D > 0` L1 data gas → fee check fails → transaction reverts in re-execution.
7. The proof attests to a revert; the original block contains a success. The proof is invalid / diverges from the committed state.

**Exact divergent value**: `get_concat_resource(&ResourceBounds{max_amount=D, max_price=P}, L1_DATA_GAS)` vs `get_concat_resource(&ResourceBounds{max_amount=0, max_price=0}, L1_DATA_GAS)` — these produce different `Felt` values, causing `get_tip_resource_bounds_hash` to return H_bounds_correct ≠ H_bounds_zero, and consequently H1 ≠ H2. [7](#0-6) [8](#0-7)

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-660)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;

        let tip = Tip(value.tip);

        let signature = TransactionSignature(
            value
                .signature
                .ok_or(missing("InvokeV3::signature"))?
                .parts
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?
                .into(),
        );

        let nonce = Nonce(value.nonce.ok_or(missing("InvokeV3::nonce"))?.try_into()?);

        let sender_address = value.sender.ok_or(missing("InvokeV3::sender"))?.try_into()?;

        let calldata =
            value.calldata.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?;

        let calldata = Calldata(calldata.into());

        let nonce_data_availability_mode =
            enum_int_to_volition_domain(value.nonce_data_availability_mode)?;

        let fee_data_availability_mode =
            enum_int_to_volition_domain(value.fee_data_availability_mode)?;

        let paymaster_data = PaymasterData(
            value.paymaster_data.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?,
        );

        let account_deployment_data = AccountDeploymentData(
            value
                .account_deployment_data
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?,
        );

        let proof_facts: ProofFacts = value
            .proof_facts
            .into_iter()
            .map(Felt::try_from)
            .collect::<Result<Vec<_>, _>>()?
            .into();

        Ok(Self {
            resource_bounds,
            tip,
            signature,
            nonce,
            sender_address,
            calldata,
            nonce_data_availability_mode,
            fee_data_availability_mode,
            paymaster_data,
            account_deployment_data,
            proof_facts,
        })
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

**File:** crates/starknet_api/src/transaction_hash.rs (L203-208)
```rust
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });
```

**File:** crates/starknet_api/src/transaction_hash.rs (L216-226)
```rust
fn get_concat_resource(
    resource_bounds: &ResourceBounds,
    resource_name: &ResourceName,
) -> Result<Felt, StarknetApiError> {
    let max_amount = resource_bounds.max_amount.0.to_be_bytes();
    let max_price = resource_bounds.max_price_per_unit.0.to_be_bytes();
    let concat_bytes =
        [[0_u8].as_slice(), resource_name.as_slice(), max_amount.as_slice(), max_price.as_slice()]
            .concat();
    Ok(Felt::from_bytes_be(&concat_bytes.try_into().expect("Expect 32 bytes")))
}
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L184-202)
```rust
    async fn convert_consensus_tx_to_internal_consensus_tx(
        &self,
        tx: ConsensusTransaction,
    ) -> TransactionConverterResult<(InternalConsensusTransaction, Option<VerifyAndStoreProofTask>)>
    {
        match tx {
            ConsensusTransaction::RpcTransaction(tx) => {
                let (internal_tx, proof_data) = self.convert_rpc_tx_to_internal(tx).await?;
                let task = proof_data.map(|(proof_facts, proof)| {
                    self.spawn_verify_and_store_proof(proof_facts, proof)
                });
                Ok((InternalConsensusTransaction::RpcTransaction(internal_tx), task))
            }
            ConsensusTransaction::L1Handler(tx) => {
                let internal_tx = self.convert_consensus_l1_handler_to_internal_l1_handler(tx)?;
                Ok((InternalConsensusTransaction::L1Handler(internal_tx), None))
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
