### Title
Gateway `check_declare_permissions` Not Enforced in Consensus Proposal Validation Path — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

The gateway enforces two declare-transaction restrictions — `block_declare` (a global kill-switch) and `authorized_declarer_accounts` (a sender whitelist) — exclusively inside `GenericGateway::add_tx_inner()`. The consensus proposal-validation path (`validate_proposal` → `handle_proposal_part` → `convert_consensus_tx_to_internal_consensus_tx`) never calls `check_declare_permissions`. A Byzantine proposer can therefore include a `Declare` transaction from an unauthorized sender (or any sender when `block_declare = true`) directly in a block proposal; every validator node will convert, forward to the batcher, and execute it without ever checking the restriction, committing an unauthorized class hash to state.

---

### Finding Description

**Direct (gateway) path — restriction enforced:**

In `crates/apollo_gateway/src/gateway.rs`, `add_tx_inner` checks declare permissions before any further processing:

```rust
if let RpcTransaction::Declare(ref declare_tx) = tx {
    if let Err(e) = self.check_declare_permissions(declare_tx) {
        metric_counters.record_add_tx_failure(&e);
        return Err(e);
    }
}
``` [1](#0-0) 

`check_declare_permissions` enforces both the `block_declare` flag and the `authorized_declarer_accounts` whitelist:

```rust
fn check_declare_permissions(&self, declare_tx: &RpcDeclareTransaction) -> Result<(), StarknetError> {
    if self.config.static_config.block_declare { return Err(...); }
    if !self.config.is_authorized_declarer(&declare_v3_tx.sender_address) { return Err(...); }
    Ok(())
}
``` [2](#0-1) 

`is_authorized_declarer` returns `false` for any address not in the whitelist when the list is set: [3](#0-2) 

**Consensus (proposal-validation) path — restriction absent:**

When a validator processes a received block proposal, `handle_proposal_part` in `validate_proposal.rs` converts each `ConsensusTransaction` directly:

```rust
let conversion_results =
    futures::future::join_all(txs.into_iter().map(|tx| {
        transaction_converter.convert_consensus_tx_to_internal_consensus_tx(tx)
    }))
    ...
let input = SendTxsForProposalInput { proposal_id, txs };
batcher.send_txs_for_proposal(input).await
``` [4](#0-3) 

`convert_consensus_tx_to_internal_consensus_tx` delegates to the shared `convert_rpc_tx_to_internal` helper: [5](#0-4) 

`convert_rpc_tx_to_internal` compiles the Sierra class and builds the internal representation — **no call to `check_declare_permissions` anywhere in this path**: [6](#0-5) 

A grep across `crates/apollo_consensus_orchestrator/` and `crates/apollo_batcher/` for `check_declare_permissions`, `block_declare`, or `authorized_declarer` returns zero matches, confirming the restriction is entirely absent from the consensus path.

---

### Impact Explanation

When `block_declare = true` or `authorized_declarer_accounts` is configured, the operator intends to prevent unauthorized class declarations from being committed to state. A Byzantine proposer can bypass this entirely by crafting a `ConsensusTransaction::RpcTransaction(RpcTransaction::Declare(...))` with an unauthorized `sender_address` and streaming it as a `ProposalPart::Transactions` batch. Every honest validator will:

1. Convert it without checking permissions.
2. Forward it to the batcher via `send_txs_for_proposal`.
3. Execute it via the blockifier, writing the class hash and compiled class hash to state.

The result is an unauthorized class declaration committed to the canonical chain — a wrong state value produced from accepted input.

**Impact category:** Critical — wrong state (unauthorized class hash / compiled class hash) committed from blockifier execution of accepted input.

---

### Likelihood Explanation

Triggering this requires a Byzantine proposer — a validator that holds proposer rights in the current consensus round. In a permissioned or early-stage network where `authorized_declarer_accounts` is actively used (as evidenced by the production config and the `UnauthorizedDeclare` error code), a single compromised or malicious validator can exploit this during any round in which it is selected as proposer. The P2P mempool-propagation path routes through the gateway (`Runner → GW_B → add_tx`) and is therefore protected; only the consensus proposal path is unprotected.

---

### Recommendation

Apply the same `check_declare_permissions` logic inside the consensus conversion path. The cleanest fix is to add a declare-permission check inside `convert_consensus_tx_to_internal_consensus_tx` (or in `handle_proposal_part` before forwarding to the batcher) using the same `block_declare` / `authorized_declarer_accounts` configuration that the gateway already reads. Alternatively, the batcher's `send_txs_for_proposal` handler could reject declare transactions whose sender is not authorized, ensuring the invariant is enforced at the point of execution regardless of which admission path was used.

---

### Proof of Concept

**Setup:** Deploy a sequencer with `block_declare = true` (or `authorized_declarer_accounts = ["0xAUTHORIZED"]`). Confirm that `gateway.add_tx(declare_tx_from_unauthorized)` returns `BLOCKED_TRANSACTION_TYPE` / `UnauthorizedDeclare`.

**Attack:**

1. A Byzantine validator (proposer in round R) constructs a `ConsensusTransaction` wrapping a `RpcDeclareTransaction::V3` with `sender_address = 0xUNAUTHORIZED`.
2. It streams this as a `ProposalPart::Transactions` batch to all peers.
3. Each honest validator's `handle_proposal_part` calls `convert_consensus_tx_to_internal_consensus_tx` — no permission check fires.
4. The converted `InternalConsensusTransaction::RpcTransaction(...)` is sent to the batcher via `send_txs_for_proposal`.
5. The batcher executes it; the blockifier writes `class_hash → compiled_class_hash` to state.
6. After `finish_proposal` and consensus commit, the unauthorized class is permanently in the canonical state, bypassing the operator's declared restriction.

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L228-233)
```rust
        if let RpcTransaction::Declare(ref declare_tx) = tx {
            if let Err(e) = self.check_declare_permissions(declare_tx) {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        }
```

**File:** crates/apollo_gateway/src/gateway.rs (L407-433)
```rust
    fn check_declare_permissions(
        &self,
        declare_tx: &RpcDeclareTransaction,
    ) -> Result<(), StarknetError> {
        // TODO(noamsp): Return same error as in Python gateway.
        if self.config.static_config.block_declare {
            return Err(StarknetError {
                code: StarknetErrorCode::UnknownErrorCode(
                    "StarknetErrorCode.BLOCKED_TRANSACTION_TYPE".to_string(),
                ),
                message: "Transaction type is temporarily blocked.".to_string(),
            });
        }
        let RpcDeclareTransaction::V3(declare_v3_tx) = declare_tx;
        if !self.config.is_authorized_declarer(&declare_v3_tx.sender_address) {
            return Err(StarknetError {
                code: StarknetErrorCode::KnownErrorCode(
                    KnownStarknetErrorCode::UnauthorizedDeclare,
                ),
                message: format!(
                    "Account address {} is not allowed to declare contracts.",
                    &declare_v3_tx.sender_address
                ),
            });
        }
        Ok(())
    }
```

**File:** crates/apollo_gateway_config/src/config.rs (L141-146)
```rust
    pub fn is_authorized_declarer(&self, declarer_address: &ContractAddress) -> bool {
        match &self.static_config.authorized_declarer_accounts {
            Some(allowed_accounts) => allowed_accounts.contains(declarer_address),
            None => true,
        }
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L598-647)
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
            };

            // Separate internal transactions from verification and store proof tasks. Each task
            // verifies the proof and stores it in the proof manager. Tasks are collected
            // and awaited later in the fin case.
            let (txs, tasks): (
                Vec<InternalConsensusTransaction>,
                Vec<Option<VerifyAndStoreProofTask>>,
            ) = conversion_results.into_iter().unzip();
            verify_and_store_proof_tasks.extend(tasks.into_iter().flatten());

            debug!(
                "Converted transactions to internal representation. hashes={:?}",
                txs.iter().map(|tx| tx.tx_hash()).collect::<Vec<TransactionHash>>()
            );

            content.push(txs.clone());
            let input = SendTxsForProposalInput { proposal_id, txs };
            let response = match batcher.send_txs_for_proposal(input).await {
                Ok(response) => response,
                Err(e) => {
                    return HandledProposalPart::Failed(format!(
                        "Failed to send transactions to batcher: {e:?}"
                    ));
                }
            };
            match response {
                SendTxsForProposalStatus::Processing => HandledProposalPart::Continue,
                SendTxsForProposalStatus::InvalidProposal(err) => HandledProposalPart::Invalid(err),
            }
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
