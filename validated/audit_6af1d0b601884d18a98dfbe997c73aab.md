Based on my investigation, I found a concrete architectural analog to the Capgo bug class: a security-enforcement flag that is checked only at one ingestion plane (the RPC/P2P submission plane) but is never re-validated at the execution/commit plane (block building, `blockifier` execution, Starknet OS re-execution).

### Title
Declare-transaction authorization control (`authorized_declarer_accounts` / `block_declare`) is enforced only at Gateway ingestion and is never re-validated during block building or execution - ([File: crates/apollo_gateway/src/gateway.rs])

### Summary
The Apollo Gateway exposes a node-level access-control feature intended to restrict which accounts may submit `Declare` transactions (`authorized_declarer_accounts`) or to fully block declares (`block_declare`). This control is enforced exactly once, in `check_declare_permissions`, called from `add_tx_inner` at RPC/P2P transaction-ingestion time. [1](#0-0) [2](#0-1)  However, the restriction is not part of the Starknet state-transition function: it is absent from the mempool's `add_tx`/`add_tx_validations`, from the Batcher/`BlockBuilder` execution path used when validating a received proposal, and from `blockifier`'s `StatefulValidator::perform_validations`, which executes `Declare` transactions unconditionally without any declarer-whitelist check. [3](#0-2) 

### Finding Description
`GatewayConfig::is_authorized_declarer` and `block_declare` are purely local, ingestion-time policy knobs enforced by `check_declare_permissions` inside `add_tx_inner`. [4](#0-3)  Once a `Declare` transaction has been accepted by *any* node's Gateway into its mempool (whether the local Gateway is configured with a looser policy, or simply a node with divergent config), it propagates via P2P and, when that node is the round's proposer, is executed and streamed as `ProposalPart::Transactions` to every validating peer. [5](#0-4)  On the validating side, `handle_proposal_part` forwards the transactions straight to `batcher.send_txs_for_proposal`, and the `BlockBuilder`/`blockifier` execution pipeline (`StatefulValidator::perform_validations` / `TransactionExecutor`) executes the `Declare` transaction with no re-check of `authorized_declarer_accounts` or `block_declare` — these fields exist solely in `apollo_gateway_config` and are never threaded into `blockifier`, the `Batcher`, or Starknet OS re-execution. [6](#0-5) [7](#0-6) 

This mirrors the reported bug class exactly: the control ("only hashed keys may authenticate" / "only whitelisted accounts may declare") is enforced on one ingress plane (PostgREST/RLS vs. Gateway HTTP+P2P `add_tx`) but is completely absent from the other plane that ultimately commits the effect (direct DB writes vs. block execution/commitment). Any component of the system that can get the transaction admitted anywhere other than through a strictly-configured Gateway — a differently configured node, a relaxed test/staging node included in the same P2P network, or simply relying on the fact that the restriction is never re-verified during block validation — results in the restriction being silently bypassed for the entire network, because all honest validators will accept and commit the block once `blockifier`/OS execution succeeds.

### Impact Explanation
If an operator relies on `authorized_declarer_accounts`/`block_declare` as a network-wide governance control (e.g., to restrict who may declare classes during a controlled launch phase), the control provides no real guarantee: it is not a protocol invariant checked during block validation, `blockifier` execution, or Starknet OS re-execution. An unauthorized declarer's transaction, once admitted into a mempool anywhere in the network, will be executed and permanently committed by all honest nodes — an unauthorized account action reaching finality despite the enforced restriction, undermining the intended access control.

### Likelihood Explanation
The check is unconditionally skipped by design in every code path except `apollo_gateway::gateway.rs::add_tx_inner`; no additional privilege or malicious behavior is required from the mempool, batcher, or blockifier — the gap is structural. The only requirement is that the transaction be admitted into some node's mempool (e.g. because that node has a different/default config where `authorized_declarer_accounts` is `None`), after which normal, honest consensus flow (`propose_block` → `validate_proposal` → `send_txs_for_proposal` → `blockifier` execution) commits it without ever consulting the restriction.

### Recommendation
Move the declarer-authorization check out of `apollo_gateway` alone and into the shared state-transition validation performed during block execution/re-execution (e.g., as part of `StatefulValidator::perform_validations` or the `BlockBuilder`/Starknet OS layer), driven by a versioned-constants-style parameter that all honest nodes and the OS agree on, so the restriction is a genuine protocol invariant rather than a per-node ingestion filter.

### Proof of Concept
1. Configure Node A's Gateway with `authorized_declarer_accounts = None` (default) and Node B's Gateway with a restrictive `authorized_declarer_accounts` allow-list, both participating in the same consensus network.
2. Submit a `Declare` transaction from an address not in Node B's allow-list directly to Node A's HTTP/RPC endpoint; `check_declare_permissions` passes on Node A and the transaction enters Node A's mempool and propagates via P2P. [8](#0-7) 
3. When Node A is selected as proposer, it includes the transaction in its `ProposalPart::Transactions`. [5](#0-4) 
4. Node B, despite its restrictive Gateway policy, validates the proposal via `batcher.send_txs_for_proposal` → `BlockBuilder` → `blockifier`, which contains no declarer-authorization check, and accepts/commits the block. [7](#0-6)

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L214-236)
```rust
    async fn add_tx_inner(
        &self,
        tx: RpcTransaction,
        p2p_message_metadata: Option<BroadcastedMessageMetadata>,
    ) -> GatewayResult<GatewayOutput> {
        let mut metric_counters = GatewayMetricHandle::new(&tx, &p2p_message_metadata);
        metric_counters.count_transaction_received();
        if let RpcTransaction::Invoke(RpcInvokeTransaction::V3(ref inv)) = tx {
            if !inv.proof_facts.is_empty() {
                metric_counters.count_private_transaction_received();
            }
        }
        let is_p2p = p2p_message_metadata.is_some();

        if let RpcTransaction::Declare(ref declare_tx) = tx {
            if let Err(e) = self.check_declare_permissions(declare_tx) {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        }

        // Perform stateless validations.
        self.stateless_tx_validator.validate(&tx)?;
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

**File:** crates/blockifier/src/blockifier/stateful_validator.rs (L68-96)
```rust
    pub fn perform_validations(&mut self, tx: AccountTransaction) -> StatefulValidatorResult<()> {
        // Deploy account transaction should be fully executed, since the constructor must run
        // before `__validate_deploy__`. The execution already includes all necessary validations,
        // so they are skipped here.
        // Declare transaction should also be fully executed - otherwise, if we only go through
        // the validate phase, we would miss the check that the class was not declared before.
        match tx.tx {
            ApiTransaction::DeployAccount(_) | ApiTransaction::Declare(_) => self.execute(tx),
            ApiTransaction::Invoke(_) => {
                let tx_context = Arc::new(self.tx_executor.block_context.to_tx_context(&tx));
                tx.perform_pre_validation_stage(self.state(), &tx_context)?;
                if !tx.execution_flags.validate {
                    return Ok(());
                }

                // `__validate__` call.
                let (_optional_call_info, actual_cost) = self.validate(&tx, tx_context.clone())?;

                // Post validations.
                PostValidationReport::verify(
                    &tx_context,
                    &actual_cost,
                    tx.execution_flags.charge_fee,
                )?;

                Ok(())
            }
        }
    }
```

**File:** crates/apollo_gateway_config/src/config.rs (L140-147)
```rust
impl GatewayConfig {
    pub fn is_authorized_declarer(&self, declarer_address: &ContractAddress) -> bool {
        match &self.static_config.authorized_declarer_accounts {
            Some(allowed_accounts) => allowed_accounts.contains(declarer_address),
            None => true,
        }
    }
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L563-611)
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
```

**File:** crates/apollo_batcher/src/batcher.rs (L541-574)
```rust
        let (block_builder, abort_signal_sender) = self
            .block_builder_factory
            .create_block_builder(
                BlockMetadata {
                    block_info: validate_block_input.block_info,
                    retrospective_block_hash: validate_block_input.retrospective_block_hash,
                },
                BlockBuilderExecutionParams {
                    deadline: deadline_as_instant(validate_block_input.deadline)?,
                    is_validator: true,
                    proposer_idle_detection_delay: self
                        .config
                        .dynamic_config
                        .proposer_idle_detection_delay_millis,
                    n_concurrent_txs: self.config.dynamic_config.n_concurrent_txs,
                    tx_polling_interval_millis: self
                        .config
                        .dynamic_config
                        .validate_tx_polling_interval_millis,
                    results_polling_interval_millis: self
                        .config
                        .dynamic_config
                        .results_polling_interval_millis,
                },
                self.config.dynamic_config.native_classes_whitelist.clone(),
                Box::new(tx_provider),
                None,
                None,
                tokio::runtime::Handle::current(),
            )
            .map_err(|err| {
                error!("Failed to get block builder: {}", err);
                BatcherError::InternalError
            })?;
```
