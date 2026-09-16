### Title
Single transaction's class-conversion failure aborts an entire in-progress block proposal - ([File: crates/apollo_batcher/src/block_builder.rs])

### Summary
The Notional report describes a fault-intolerance bug class: a function processes a batch of independent operations (redemptions from Compound/Aave/Euler) via a strict all-or-nothing loop, so a single failing sub-operation aborts the whole batch even though the rest could succeed independently. The Apollo sequencer's `BlockBuilder::add_txs_to_executor` exhibits the same "batch of independent operations, one failure kills all" pattern when converting mempool transactions to executable Blockifier transactions before block building.

### Finding Description
`add_txs_to_executor` fetches a chunk of transactions from the mempool/tx provider and converts each one into an executable Blockifier transaction using `futures::future::try_join_all`: [1](#0-0) 

`try_join_all` returns as soon as any single future errors, discarding all other (already fetched, otherwise convertible) transactions' results, and propagates the error with `?` out of `add_txs_to_executor`: [2](#0-1) 

This error bubbles up through `build_block_inner`'s main loop via `?` on `self.add_txs_to_executor().await?`: [3](#0-2) 

which causes `build_block` to treat the whole block-building attempt as failed and call `abort_block()` on the executor, discarding all transactions already accepted and being executed in that proposal round: [4](#0-3) 

The error type causing this is `TransactionConverterError`, which is a first-class variant of `BlockBuilderError` (via `#[from]`), meaning any conversion failure for even one transaction directly fails the block build: [5](#0-4) 

Because `convert_to_executable_blockifier_tx` depends on external state such as the class manager (e.g., resolving declared classes), a single transaction whose class lookup or conversion transiently fails (analogous to one "money market" temporarily failing) causes the entire currently-forming block/proposal to be aborted, even though every other transaction in the batch (and previously accepted transactions in that block) could have executed successfully.

### Impact Explanation
This is directly reachable by any account submitting an ordinary transaction to the mempool — no privileged operator or malicious peer role is required. An attacker (or even an unlucky legitimate user) can submit a transaction whose conversion transiently fails (e.g., a declare/invoke transaction whose referenced class temporarily can't be resolved from the class manager) while it's mixed into a batch with many other honest transactions. Instead of only rejecting/deferring the single problematic transaction, the whole block proposal under construction is aborted (`abort_block`), discarding the batcher's work on all other transactions already added to that block. Repeated at scale, this degrades block production liveness/throughput — the network's ability to confirm new transactions in a timely manner is impacted, which matches the impact criteria for "network unable to confirm new transactions" in a lesser/repeated form.

### Likelihood Explanation
Likelihood is dependent on how often `convert_to_executable_blockifier_tx` transiently fails for individual transactions in the proposer/validator flow (e.g., class manager lookups racing with declare propagation, or other transient errors surfaced as `TransactionConverterError`). Since transaction conversion touches external dependent services (class manager), transient failures are plausible during normal network operation, and a single such failure is amplified into a full block-abort rather than being isolated to the offending transaction, which is the exact fault-tolerance gap identified in the analog report.

### Recommendation
Do not use `try_join_all`/`?`-propagation across a batch of independent transaction conversions. Instead, convert each transaction with its own `Result` (à la `join_all` + per-item retry/skip), and for each transaction that fails conversion: reject/defer only that transaction (e.g., report a per-tx failure or drop it back to the mempool) while proceeding with the rest of the successfully converted transactions being sent to the executor. This preserves the proposal round instead of aborting the whole block builder on a single transient conversion error.

### Proof of Concept
Not applicable in the traditional sense (this is a code-review/logic finding, not an exploit requiring a PoC transaction beyond what is already described): submit a transaction whose class resolution via `TransactionConverter` fails transiently (e.g., points at a class hash not yet visible to the class manager) alongside a batch of otherwise valid transactions in `add_txs_to_executor`; observe via `crates/apollo_batcher/src/block_builder.rs` that `try_join_all` fails the whole `Vec` conversion, `add_txs_to_executor` returns `Err(BlockBuilderError::TransactionConverterError(_))`, and `build_block` aborts the entire block via `abort_block()`, as also demonstrated by the existing test `convert_internal_consensus_tx_to_consensus_tx_fail` for the analogous reproposal-conversion path [6](#0-5) .

### Citations

**File:** crates/apollo_batcher/src/block_builder.rs (L85-103)
```rust
#[derive(Debug, Error)]
pub enum BlockBuilderError {
    #[error(transparent)]
    BlockifierStateError(#[from] StateError),
    #[error(transparent)]
    ExecutorError(#[from] BlockifierTransactionExecutorError),
    #[error(transparent)]
    GetTransactionError(#[from] TransactionProviderError),
    #[error(transparent)]
    StreamTransactionsError(
        #[from] Box<tokio::sync::mpsc::error::SendError<InternalConsensusTransaction>>,
    ),
    #[error(transparent)]
    FailOnError(FailOnErrorCause),
    #[error("The block builder was aborted.")]
    Aborted,
    #[error(transparent)]
    TransactionConverterError(#[from] TransactionConverterError),
}
```

**File:** crates/apollo_batcher/src/block_builder.rs (L304-318)
```rust
#[async_trait]
impl BlockBuilderTrait for BlockBuilder {
    async fn build_block(&mut self) -> BlockBuilderResult<BlockExecutionArtifacts> {
        let res = self.build_block_inner().await;
        if res.is_err() {
            let executor = self.executor.clone();
            spawn_blocking(move || {
                let mut locked_executor = executor.blocking_lock();
                locked_executor.abort_block();
            })
            .await
            .expect("Aborting block should succeed.");
        }
        res
    }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L379-388)
```rust
            let now = tokio::time::Instant::now();
            if now >= next_mempool_poll_at {
                match self.add_txs_to_executor().await? {
                    // Keep draining while txs are flowing: re-poll next iteration without sleeping.
                    AddTxsToExecutorResult::NewTxs => continue,
                    AddTxsToExecutorResult::NoNewTxs => {
                        next_mempool_poll_at = now + tx_polling_interval;
                    }
                }
            }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L482-528)
```rust
    /// Adds new transactions (if there are any) from `tx_provider` to the executor.
    ///
    /// Returns whether new transactions were added and whether the transaction stream is exhausted
    /// (this can only happen in validator mode).
    async fn add_txs_to_executor(&mut self) -> BlockBuilderResult<AddTxsToExecutorResult> {
        // Restrict the number of transactions to fetch such that the number of transactions in
        // progress is at most `n_concurrent_txs`.
        let n_concurrent_txs = self.execution_params.n_concurrent_txs;
        let n_txs_to_fetch = n_concurrent_txs - min(self.n_txs_in_progress(), n_concurrent_txs);

        if n_txs_to_fetch == 0 {
            return Ok(AddTxsToExecutorResult::NoNewTxs);
        }

        let next_txs = match self.tx_provider.get_txs(n_txs_to_fetch).await {
            Err(e @ TransactionProviderError::L1HandlerTransactionValidationFailed { .. })
                if self.execution_params.is_validator =>
            {
                warn!("Failed to validate L1 Handler transaction: {:?}", e);
                return Err(BlockBuilderError::FailOnError(L1HandlerTransactionValidationFailed(
                    e,
                )));
            }
            Err(err) => {
                error!("Failed to get transactions from the transaction provider: {:?}", err);
                return Err(err.into());
            }
            Ok(result) => result,
        };

        if next_txs.is_empty() {
            return Ok(AddTxsToExecutorResult::NoNewTxs);
        }

        let n_txs = next_txs.len();
        debug!(
            "Got {} transactions from the transaction provider (aggregated: {}).",
            n_txs,
            self.block_txs.len() + n_txs
        );

        self.block_txs.extend(next_txs.iter().cloned());

        let tx_convert_futures = next_txs.iter().map(|tx| async {
            convert_to_executable_blockifier_tx(&self.transaction_converter, tx.clone()).await
        });
        let executor_input_chunk = futures::future::try_join_all(tx_convert_futures).await?;
```

**File:** crates/apollo_consensus_orchestrator/src/build_proposal_test.rs (L94-113)
```rust
#[tokio::test]
async fn convert_internal_consensus_tx_to_consensus_tx_fail() {
    let (mut proposal_args, _proposal_receiver) = create_proposal_build_arguments();
    // Setup batcher to return Ok on propose_block and TX from get_proposal_content.
    proposal_args.deps.batcher.expect_propose_block().returning(|_| Ok(()));
    proposal_args.deps.batcher.expect_get_proposal_content().times(1).returning(|_| {
        Ok(GetProposalContentResponse {
            content: GetProposalContent::Txs(INTERNAL_TX_BATCH.clone()),
        })
    });
    // Overwrite the transaction converter to return an error, since by default it returns Ok.
    let mut transaction_converter = MockTransactionConverterTrait::new();
    transaction_converter.expect_convert_internal_consensus_tx_to_consensus_tx().returning(|_| {
        Err(TransactionConverterError::ClassNotFound { class_hash: ClassHash::default() })
    });
    proposal_args.deps.transaction_converter = transaction_converter;

    let res = build_proposal(proposal_args.into()).await;
    assert!(matches!(res, Err(BuildProposalError::TransactionConverterError(_))));
}
```
