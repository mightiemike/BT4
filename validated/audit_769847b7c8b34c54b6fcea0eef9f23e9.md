### Title
Single failing transaction conversion aborts an entire in-progress block/proposal - (File: crates/apollo_batcher/src/block_builder.rs)

### Summary
`BlockBuilder::add_txs_to_executor` fetches a chunk of transactions from the mempool/L1-provider and converts *all* of them in one `futures::future::try_join_all` call. If conversion of a single transaction in the chunk fails (e.g. a `Declare` transaction whose class cannot be resolved by the class manager, yielding `TransactionConverterError::ClassNotFound`), the whole `try_join_all` fails and the `?` operator propagates the error out of `add_txs_to_executor` and `build_block_inner`. `build_block()` then treats this as a fatal error, calls `abort_block()` on the executor, and returns an error - discarding the entire block being built, including every transaction that was already successfully fetched, converted, executed and added to the block in earlier iterations of the same proposal round. This mirrors the audit finding's core defect: a failure in one "action"/item of a batch causes the whole batch (here, the whole proposal-building attempt) to be discarded rather than being isolated to the offending item.

### Finding Description
`add_txs_to_executor` ( [1](#0-0) ) fetches `next_txs` from the transaction provider and immediately appends them to `self.block_txs`, then converts every transaction in the chunk concurrently:

```
let tx_convert_futures = next_txs.iter().map(|tx| async {
    convert_to_executable_blockifier_tx(&self.transaction_converter, tx.clone()).await
});
let executor_input_chunk = futures::future::try_join_all(tx_convert_futures).await?;
``` [2](#0-1) 

`try_join_all` fails as soon as any single future errors, and via `#[from] TransactionConverterError` on `BlockBuilderError` ( [3](#0-2) ) this error is propagated as-is - there is no per-transaction try/catch or isolation. The error then bubbles up through `add_txs_to_executor().await?` in `build_block_inner`'s main loop ( [4](#0-3) ), and is finally handled by `build_block`:

```
async fn build_block(&mut self) -> BlockBuilderResult<BlockExecutionArtifacts> {
    let res = self.build_block_inner().await;
    if res.is_err() {
        ...
        locked_executor.abort_block();
        ...
    }
    res
}
``` [5](#0-4) 

This means every transaction already added to the executor in prior loop iterations of the same proposal (which may have consumed significant compute and been legitimately valid/executable) is discarded together with the single bad transaction. `convert_internal_rpc_tx_to_executable_tx`/`convert_internal_consensus_tx_to_executable_tx` in the transaction converter can return `ClassNotFound` for a `Declare` transaction whenever the class manager does not currently have the class ( [6](#0-5)  and [7](#0-6) ), which is directly reachable by any unprivileged declarer whose declared class is not (yet, or no longer) resolvable by the receiving node's class manager (e.g. after p2p propagation/consensus timing gaps between mempool sync and class-manager sync across nodes).

Crucially, this failure path does not remove the offending transaction from the mempool (removal on rejection only happens via `commit_proposal_and_block`/mempool `commit_block`, reached only after a block is successfully committed - see [8](#0-7)  and [9](#0-8) ). Consequently the same problematic transaction can be re-fetched by the mempool on the very next proposal attempt (by this node or peers with the same class-visibility gap), repeatedly aborting block building.

### Impact Explanation
An abort discards an entire in-progress proposal (all previously executed and valid transactions in that round), forcing costly re-execution and, if the underlying cause (unresolved class) recurs across consecutive proposal rounds/validators, can repeatedly deny successful block production - a liveness degradation of the "network unable to confirm new transactions" class described in the validation rules. This is caused entirely by a single unprivileged declared class becoming unresolvable to the class manager at the moment of block building, not by any operator/peer misbehavior.

### Likelihood Explanation
Any account can submit a `Declare` transaction (an unprivileged, ordinarily reachable transaction type). The `ClassNotFound` failure mode is not gated behind any special privilege and is a natural consequence of the class-manager/mempool synchronization model across distributed sequencer nodes; it does not require an attacker to corrupt state, only to have their declared class be transiently unresolvable relative to the specific node performing block building (e.g., due to p2p propagation lag).

### Recommendation
Isolate per-transaction conversion failures from the overall batch/block outcome: convert transactions with individual error handling (e.g. `join_all` returning `Result` per item instead of `try_join_all`), drop or defer only the offending transaction (and remove/flag it appropriately), and continue block building/execution with the remaining valid transactions in the chunk instead of aborting the whole block via `abort_block()`.

### Proof of Concept
1. A user submits a valid `Declare` transaction that is accepted into the mempool and propagated via p2p to other sequencer nodes.
2. A node acting as proposer/validator begins block building and reaches `add_txs_to_executor`, fetching a chunk including this Declare transaction along with several other, unrelated valid transactions ( [10](#0-9) ).
3. Because that node's class manager has not yet resolved the declared class (e.g. p2p/class-sync timing), `convert_to_executable_blockifier_tx` returns `TransactionConverterError::ClassNotFound` for that one transaction ( [7](#0-6) ).
4. `try_join_all` fails, the `?` propagates through `add_txs_to_executor` and `build_block_inner`, and `build_block` calls `abort_block()`, discarding the entire block, including all previously executed valid transactions in this proposal round ( [11](#0-10) ).
5. Since the offending transaction is not removed from the mempool at this point, it can be fetched again on the next proposal attempt, repeating the abort.

### Citations

**File:** crates/apollo_batcher/src/block_builder.rs (L97-103)
```rust
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

**File:** crates/apollo_batcher/src/block_builder.rs (L379-389)
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
            self.sleep(now, next_mempool_poll_at).await;
```

**File:** crates/apollo_batcher/src/block_builder.rs (L486-528)
```rust
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L130-138)
```rust
    async fn get_sierra(
        &self,
        class_hash: ClassHash,
    ) -> TransactionConverterResult<SierraContractClass> {
        self.class_manager_client
            .get_sierra(class_hash)
            .await?
            .ok_or(TransactionConverterError::ClassNotFound { class_hash })
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L267-296)
```rust
    async fn convert_internal_rpc_tx_to_executable_tx(
        &self,
        InternalRpcTransaction { tx, tx_hash }: InternalRpcTransaction,
    ) -> TransactionConverterResult<AccountTransaction> {
        match tx {
            InternalRpcTransactionWithoutTxHash::Invoke(tx) => {
                Ok(AccountTransaction::Invoke(executable_transaction::InvokeTransaction {
                    tx: tx.into(),
                    tx_hash,
                }))
            }
            InternalRpcTransactionWithoutTxHash::Declare(tx) => {
                let (sierra, contract_class) = tokio::try_join!(
                    self.get_sierra(tx.class_hash),
                    self.get_executable(tx.class_hash)
                )?;
                let class_info = ClassInfo {
                    contract_class,
                    sierra_program_length: sierra.sierra_program.len(),
                    abi_length: sierra.abi.len(),
                    sierra_version: SierraVersion::extract_from_program(&sierra.sierra_program)?,
                };

                Ok(AccountTransaction::Declare(executable_transaction::DeclareTransaction {
                    tx: tx.into(),
                    tx_hash,
                    class_info,
                }))
            }
            InternalRpcTransactionWithoutTxHash::DeployAccount(
```

**File:** crates/apollo_batcher/src/batcher.rs (L1216-1224)
```rust
        if let Some(mempool_client) = &self.mempool_client {
            let mempool_result = mempool_client
                .commit_block(CommitBlockArgs { address_to_nonce, rejected_tx_hashes })
                .await;
            if let Err(mempool_err) = mempool_result {
                // Recoverable error, mempool won't be updated with the new block.
                error!("Failed to commit block to mempool: {}", mempool_err);
            }
        }
```

**File:** crates/apollo_mempool/src/mempool.rs (L548-583)
```rust
    fn remove_rejected_txs(
        &mut self,
        rejected_tx_hashes: IndexSet<TransactionHash>,
        rewound_tx_hashes: &IndexSet<TransactionHash>,
    ) -> AddressToNonce {
        if !rejected_tx_hashes.is_empty() {
            debug!("Removed rejected transactions from mempool: {:?}", rejected_tx_hashes);
        }
        let mut rejected_txs_counter = 0;
        let mut account_nonce_updates = AddressToNonce::new();

        for tx_hash in rejected_tx_hashes {
            // In FIFO mode, if a rejected transaction was rewound, skip removal (keep in pool and
            // queue). Otherwise, remove it from both pool and queue.
            if rewound_tx_hashes.contains(&tx_hash) {
                continue;
            }

            if let Ok(tx) = self.tx_pool.remove(tx_hash) {
                self.tx_queue.remove_by_address(tx.contract_address());
                rejected_txs_counter += 1;
                self.decrement_stuck_txs_if_gap_account(tx.contract_address(), 1);
                account_nonce_updates
                    .entry(tx.contract_address())
                    .and_modify(|nonce| *nonce = (*nonce).min(tx.nonce()))
                    .or_insert(tx.nonce());
            } else {
                continue; // Transaction hash unknown to mempool, from a different node.
            }

            // TODO(clean_accounts): remove address with no transactions left after a block cycle /
            // TTL.
        }
        metric_count_rejected_txs(rejected_txs_counter);
        account_nonce_updates
    }
```
