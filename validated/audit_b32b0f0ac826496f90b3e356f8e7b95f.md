### Title
`compiled_class_hash` Validation Uniformly Applied in Both Gateway and Consensus Conversion Paths, Causing Proposal Rejection on Compiler Version Boundary — (`crates/apollo_transaction_converter/src/transaction_converter.rs`)

---

### Summary

`convert_rpc_tx_to_internal()` applies a `compiled_class_hash` equality check against the locally-compiled `executable_class_hash_v2` for every Declare transaction it processes. This function is shared by both the external gateway path and the internal consensus validation path. When a validator node's class manager compiles the same Sierra program to a different CASM hash than the proposer's (e.g., due to a compiler version boundary during a rolling upgrade), the check fails and the entire block proposal is marked `Failed`, not `Invalid`. This is the direct sequencer analog of the external report: a user-facing validation gate is applied uniformly even in a privileged internal path (consensus), permanently blocking valid proposals from being accepted.

---

### Finding Description

In `convert_rpc_tx_to_internal()`, the Declare branch calls `add_class` on the local class manager and then enforces:

```rust
if tx.compiled_class_hash != executable_class_hash_v2 {
    return Err(TransactionConverterError::ValidateCompiledClassHashError(
        ValidateCompiledClassHashError::CompiledClassHashMismatch {
            computed_class_hash: executable_class_hash_v2,
            supplied_class_hash: tx.compiled_class_hash,
        },
    ));
}
``` [1](#0-0) 

This private helper is called from two public entry points:

1. **Gateway path** — `convert_rpc_tx_to_internal_rpc_tx()` (line 256–265): appropriate, because the user-supplied hash must be validated against the local compiler before the transaction is admitted.

2. **Consensus path** — `convert_consensus_tx_to_internal_consensus_tx()` (line 184–202): the same check is re-run on a transaction that was already validated and accepted by the proposer's gateway. [2](#0-1) 

When the consensus path fails this check, `validate_proposal.rs` returns `HandledProposalPart::Failed` — not `HandledProposalPart::Invalid`:

```rust
Err(e) => {
    return HandledProposalPart::Failed(format!(
        "Failed to convert transactions. Stopping the build of the current \
         proposal. {e:?}"
    ));
}
``` [3](#0-2) 

`Failed` signals an internal processing error on the validator, not a protocol-level invalid proposal. The validator stops building the proposal for that round. Because the check is re-run against the validator's own compiler output rather than trusting the hash already embedded in the transaction (and already committed to the transaction hash), any compiler version skew between proposer and validator causes this divergence.

The `compiled_class_hash` is part of the transaction hash preimage: [4](#0-3) 

So the proposer's gateway computed and stored `tx_hash` using `tx.compiled_class_hash`. The validator re-derives `executable_class_hash_v2` locally and compares it against `tx.compiled_class_hash`. If they differ, the validator rejects the proposal even though the transaction hash is internally consistent and the proposer's gateway legitimately accepted it.

---

### Impact Explanation

**High — Consensus admission rejects valid proposals / transaction conversion binds the wrong executable payload.**

A validator running a different Sierra-to-CASM compiler version than the proposer will reject every block proposal that contains a Declare V3 transaction. The rejection is `Failed` (internal error), not `Invalid` (bad proposal), so the validator cannot vote on the proposal and effectively drops out of that consensus round. During a rolling upgrade where nodes are on different compiler versions, this can stall block production for any block containing a Declare transaction. An unprivileged user can trigger this by submitting any valid Declare transaction to the proposer's gateway during the version-skew window.

---

### Likelihood Explanation

**Medium.** The trigger condition — compiler version skew between proposer and validator — occurs naturally during any rolling upgrade of the Sierra-to-CASM compiler. No special attacker capability is required beyond submitting a standard Declare V3 transaction. The window exists for the entire duration of the upgrade rollout.

---

### Recommendation

The `compiled_class_hash` equality check against the locally-compiled output belongs exclusively in the **gateway path**. In the **consensus path**, the transaction has already been validated by the proposer's gateway and its hash has been committed. The consensus converter should trust `tx.compiled_class_hash` as authoritative and skip the local recompilation check, or alternatively, the consensus path should use a separate conversion function that does not re-run user-facing admission checks.

Concretely, split `convert_rpc_tx_to_internal` into two variants:
- One for the gateway path that includes the `compiled_class_hash` equality check.
- One for the consensus path that calls `add_class` only to register the class locally (idempotent) but does **not** compare the returned hash against `tx.compiled_class_hash`.

---

### Proof of Concept

1. Node A (proposer) runs compiler version `X`; Node B (validator) runs compiler version `Y ≠ X`.
2. A user submits a valid Declare V3 transaction to Node A's gateway with `compiled_class_hash = H_X` (the hash produced by compiler `X`).
3. Node A's gateway calls `convert_rpc_tx_to_internal` → `add_class` → gets `executable_class_hash_v2 = H_X` → check passes → transaction admitted, `tx_hash` computed over `H_X`.
4. Node A includes the transaction in a block proposal and broadcasts it via consensus.
5. Node B receives the proposal and calls `convert_consensus_tx_to_internal_consensus_tx` → `convert_rpc_tx_to_internal` → `add_class` → gets `executable_class_hash_v2 = H_Y ≠ H_X`.
6. The check `tx.compiled_class_hash (H_X) != executable_class_hash_v2 (H_Y)` fires → `TransactionConverterError::ValidateCompiledClassHashError` is returned.
7. `validate_proposal.rs` returns `HandledProposalPart::Failed` — Node B cannot process the proposal and drops out of the consensus round.
8. Any block containing a Declare transaction during the version-skew window is unprocessable by Node B, stalling consensus. [5](#0-4) [6](#0-5)

### Citations

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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L334-393)
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
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L598-616)
```rust
        Some(ProposalPart::Transactions(TransactionBatch { transactions: txs })) => {
            // TODO(guyn): check that the length of txs and the number of batches we receive is not
            // so big it would fill up the memory (in case of a malicious proposal)
            debug!("Received transaction batch with {} txs", txs.len());
            let conversion_results =
                futures::future::join_all(txs.into_iter().map(|tx| {
                    transaction_converter.convert_consensus_tx_to_internal_consensus_tx(tx)
                }))
                .await
                .into_iter()
                .collect::<Result<Vec<_>, _>>();
            let conversion_results = match conversion_results {
                Ok(results) => results,
                Err(e) => {
                    return HandledProposalPart::Failed(format!(
                        "Failed to convert transactions. Stopping the build of the current \
                         proposal. {e:?}"
                    ));
                }
```
