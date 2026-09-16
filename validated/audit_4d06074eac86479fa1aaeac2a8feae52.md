### Title
Authorized-declarer whitelist enforced only at Gateway ingestion, bypassed entirely during consensus proposal validation - (File: crates/apollo_consensus_orchestrator/src/validate_proposal.rs)

### Summary
`GatewayConfig::is_authorized_declarer` is a per-node, optional whitelist restricting which accounts may submit `Declare` transactions [1](#0-0) . This restriction is checked exactly once, in `check_declare_permissions`, which is only invoked from `GenericGateway::add_tx_inner` when a transaction is first ingested via RPC or via the mempool P2P transaction-propagation path (which itself re-enters the gateway's `add_tx`) [2](#0-1) [3](#0-2) . No equivalent check exists anywhere in the batcher, blockifier execution path, or the Starknet OS declare-transaction handling [4](#0-3) .

### Finding Description
When a validator node validates a received block proposal, transactions arrive as `ProposalPart::Transactions` over the consensus network stream and are handed directly to the batcher via `send_txs_for_proposal`/`SendTxsForProposalInput`, completely independent of the gateway's `add_tx` pipeline [5](#0-4) . The conversion step (`convert_consensus_tx_to_internal_consensus_tx`) and the batcher's `validate_block`/block-builder execution path never call `check_declare_permissions` or `is_authorized_declarer`; the only two crates referencing `authorized_declarer_accounts` in the whole codebase are `apollo_gateway_config` and `apollo_gateway` (plus deployment config and tests), confirming the enforcement is confined to gateway ingestion.

Consequently, `authorized_declarer_accounts` is not a protocol-level/consensus-level rule; it is a purely local, node-configurable filter applied only to transactions entering that specific node's mempool through its own gateway. A proposer node — even one that does not configure `authorized_declarer_accounts` (or configures it more permissively) — can include an "unauthorized" declare transaction in a block it proposes. When other validating nodes run `validate_proposal` → `handle_proposal_part` → `batcher.send_txs_for_proposal`, the transaction is executed and committed to the block without ever being re-checked against that validator's own `authorized_declarer_accounts` list, since that check simply does not exist on the proposal-validation code path.

### Impact Explanation
This mirrors the reported bug class exactly: a restriction meant to gate a specific action (minting in the report; declaring a class here) is enforced only at one entry point (`ArrakisV2Router`/the Gateway) while the underlying execution engine (the vault's `mint()`/the batcher+blockifier) performs the action unconditionally. Any account not on a node's declarer whitelist can still get a class declared and committed to the chain, as long as the transaction reaches any proposer (or that node's own mempool) that will place it into a proposal — an outcome fully reachable by an ordinary, unprivileged transaction sender simply by submitting the declare transaction (or having it propagate) to a node without the restriction, and waiting for that node's turn to propose. This causes unauthorized account action (declaring a class despite being disallowed) to be committed network-wide, defeating the purpose of the `authorized_declarer_accounts`/`block_declare` controls for every other node.

### Likelihood Explanation
Likely, since `authorized_declarer_accounts` is documented as an operator-configurable option (default `None`/unrestricted) meant to throttle or gate class declarations [6](#0-5) ; nothing prevents heterogeneous configuration across the node fleet, and consensus by design accepts proposals from any active proposer in rotation. No malicious proposer or operator behavior is required — a normal user submitting a declare transaction that reaches any single node lacking the restriction (or that later gets included via the batcher directly, since the sending node's own mempool has no restriction) is sufficient to have it committed to the chain that all other nodes must accept as valid.

### Recommendation
Move the authorized-declarer enforcement out of the Gateway-only ingestion path and into a place that is checked uniformly by every node during block validation/execution — e.g., as part of `AccountTransaction` pre-validation in `blockifier`, or within `validate_block`/`handle_proposal_part` in the batcher/consensus-orchestrator — so that it is applied consistently regardless of how a transaction was learned about (RPC, P2P mempool propagation, or via a received block proposal). Alternatively, treat this restriction explicitly as a "soft" mempool-admission-only control (not a security boundary) and document that it provides no protocol-level guarantee.

### Proof of Concept
1. Configure Node A's `gateway_config.static_config.authorized_declarer_accounts` to restrict declares to `{0x1}` [7](#0-6) .
2. Configure Node B (or leave default) with `authorized_declarer_accounts = None` (unrestricted) [8](#0-7) .
3. An account not in `{0x1}` submits a `Declare` transaction to Node B; Node B's gateway accepts it into its mempool since `is_authorized_declarer` returns `true` for `None` [9](#0-8) .
4. When Node B is the round's proposer, it includes this declare transaction in `ProposalPart::Transactions`.
5. Node A, validating the proposal, processes the transaction purely through `handle_proposal_part`/`send_txs_for_proposal` → batcher → blockifier, without any call to `check_declare_permissions` [5](#0-4) , and accepts/commits the block containing the "unauthorized" declare.

### Citations

**File:** crates/apollo_gateway_config/src/config.rs (L49-58)
```rust
    pub block_declare: bool,
    #[serde(default, deserialize_with = "deserialize_comma_separated_str")]
    pub authorized_declarer_accounts: Option<Vec<ContractAddress>>,
    /// Maximum number of Sierra-to-CASM compilations (triggered by declare transactions) allowed
    /// to run concurrently. Declares that arrive while this limit is reached are rejected
    /// immediately rather than queued.
    #[validate(range(min = 1))]
    pub max_concurrent_declare_compilations: usize,
    pub proof_archive_writer_config: ProofArchiveWriterConfig,
}
```

**File:** crates/apollo_gateway_config/src/config.rs (L60-76)
```rust
impl Default for GatewayStaticConfig {
    fn default() -> Self {
        Self {
            stateless_tx_validator_config: StatelessTransactionValidatorConfig::default(),
            stateful_tx_validator_config: StatefulTransactionValidatorConfig::default(),
            contract_class_manager_config: ContractClassManagerConfig {
                contract_cache_size: 300,
                ..Default::default()
            },
            chain_info: ChainInfo::default(),
            block_declare: false,
            authorized_declarer_accounts: None,
            max_concurrent_declare_compilations: DEFAULT_MAX_CONCURRENT_DECLARE_COMPILATIONS,
            proof_archive_writer_config: ProofArchiveWriterConfig::default(),
        }
    }
}
```

**File:** crates/apollo_gateway_config/src/config.rs (L140-146)
```rust
impl GatewayConfig {
    pub fn is_authorized_declarer(&self, declarer_address: &ContractAddress) -> bool {
        match &self.static_config.authorized_declarer_accounts {
            Some(allowed_accounts) => allowed_accounts.contains(declarer_address),
            None => true,
        }
    }
```

**File:** crates/apollo_gateway/src/gateway.rs (L214-233)
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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L878-912)
```rust
impl<U: UpdatableState> ExecutableTransaction<U> for AccountTransaction {
    fn execute_raw(
        &self,
        state: &mut TransactionalState<'_, U>,
        block_context: &BlockContext,
        concurrency_mode: bool,
    ) -> TransactionExecutionResult<TransactionExecutionInfo> {
        let tx_context = Arc::new(block_context.to_tx_context(self));
        self.verify_tx_version(tx_context.tx_info.version())?;

        // Do not run validate or perform any account-related actions for declare transactions that
        // meet the following conditions.
        // This flow is used for the sequencer to bootstrap a new system.
        // Note: The absence of any account-related action leads to some unintuitive but expected
        // behavior:
        // - After the transaction is executed successfully, the batcher does not notify the mempool
        //   about its inclusion in a block. As a result, the transaction remains in the mempool.
        // - When the next block is produced, the mempool will propose the same transaction again.
        // - This time, execution will fail because the contract has already been declared.
        // - The transaction will then be marked as rejected, the mempool will be notified, and the
        //   transaction will be removed from the mempool.
        if let Transaction::Declare(tx) = &self.tx {
            if tx.is_bootstrap_declare(self.execution_flags.charge_fee) {
                let mut context = EntryPointExecutionContext::new_invoke(
                    tx_context.clone(),
                    self.execution_flags.charge_fee,
                    SierraGasRevertTracker::new(GasAmount::default()),
                );
                let mut remaining_gas = 0;
                let res = tx.run_execute(state, &mut context, &mut remaining_gas)?;
                assert!(res.is_none(), "Declare execute should not result in a CallInfo.");

                return Ok(TransactionExecutionInfo::default());
            }
        }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L563-612)
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

**File:** crates/apollo_gateway/src/gateway_test.rs (L798-820)
```rust
#[rstest]
#[tokio::test]
async fn test_unauthorized_declare_config(mut mock_dependencies: MockDependencies) {
    let authorized_address = contract_address!("0x1");
    mock_dependencies.config.static_config.authorized_declarer_accounts =
        Some(vec![authorized_address]);

    let gateway = mock_dependencies.gateway();
    let rpc_declare_tx = declare_tx();

    // Ensure the sender address is different from the authorized address.
    assert_ne!(
        rpc_declare_tx.calculate_sender_address().unwrap(),
        authorized_address,
        "Sender address should not be authorized"
    );

    let gateway_output_code_error = gateway.add_tx(rpc_declare_tx, None).await.unwrap_err().code;
    let expected_code_error =
        StarknetErrorCode::KnownErrorCode(KnownStarknetErrorCode::UnauthorizedDeclare);

    assert_eq!(gateway_output_code_error, expected_code_error);
}
```
