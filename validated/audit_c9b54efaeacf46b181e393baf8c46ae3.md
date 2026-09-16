### Title
DOS in `ValidateTransactionProvider::get_txs` — single invalid L1Handler transaction aborts an entire batch validation - (File: crates/apollo_batcher/src/transaction_provider.rs)

### Summary
`ValidateTransactionProvider::get_txs` drains up to `n_txs` `InternalConsensusTransaction`s from the consensus channel via `recv_many`, then iterates the batch validating any `L1Handler` transactions against the local `L1EventsProviderClient`. If any single transaction in that batch is deemed `Invalid` — including due to a transient local error from the L1 events provider client itself — the function immediately returns `Err(...)`, discarding the entire already-drained batch instead of processing/returning the remaining valid transactions. This mirrors the reported `delegate_compound` bug class: iterating over a set of items and letting one failure abort the whole batch operation, rather than isolating per-item failures.

### Finding Description
In `crates/apollo_batcher/src/transaction_provider.rs`: [1](#0-0) 

The loop calls `self.l1_events_provider_client.validate(tx.tx_hash, self.height)` for each `L1Handler` transaction in the buffer. Critically, any error from the client call itself (not just a legitimate "invalid" classification of the transaction) is coerced into `L1ValidationStatus::Invalid(L1InvalidValidationStatus::L1EventsProviderError)`: [2](#0-1) 

As soon as one transaction in the batch is `Invalid` for any reason (including this transient-error fallback), the function returns `Err(TransactionProviderError::L1HandlerTransactionValidationFailed { .. })` immediately, without returning the transactions already pulled out of `tx_receiver` via `recv_many` (`buffer` is dropped). This error type is wired into the batcher's block-builder error enum: [3](#0-2) 

with `FailOnErrorCause::L1HandlerTransactionValidationFailed(TransactionProviderError)` used to fail block validation outright, matching the same "abort whole loop on first failure" pattern flagged in the source Kintsu report about `delegate_compound`, as opposed to the safer pattern already used elsewhere in this codebase (e.g. `TransactionExecutor::execute_txs_sequentially_inner`, which records per-transaction errors individually and only breaks the loop on `BlockFull`): [4](#0-3) 

### Impact Explanation
An L1Handler transaction is a message reachable directly from an L1 sender. Because a validating node's `validate()` call outcome depends on that node's own local `L1EventsProviderClient` state/health (subject to transient RPC blips, timing races between propose/validate on consumption/cancellation windows, etc.), different honest validators processing the same proposed block can non-deterministically reach different `Invalid` verdicts for the same L1Handler transaction. Since a single `Invalid` verdict aborts validation of the *entire* transaction batch fetched in that `get_txs` call (not just the offending transaction), an honest proposer's otherwise-valid block/batch can be rejected by some validators while accepted by others. This can manifest as honest-node divergence on block validation and repeated proposal rejection, degrading the network's ability to confirm new blocks/transactions during periods when the L1 events subsystem is flaky for a subset of nodes.

### Likelihood Explanation
Reaching this path requires nothing beyond a normal L1Handler transaction (an "L1 message") being included in a proposal and reaching a validator whose `L1EventsProviderClient` transiently errors or observes a race (e.g., consumption/cancellation timing) at validation time — no malicious operator, proposer, or peer is required. This is a plausible, naturally-occurring transient condition rather than an attacker-crafted exploit, making it moderately likely under normal network operation with L1 connectivity issues.

### Recommendation
Do not let a single transaction's validation failure discard the whole already-dequeued batch. Options:
1. On `Invalid` status for one L1Handler tx, only fail/exclude that specific transaction (and transactions after it that depend on it), returning the rest of the buffer that validated successfully, similar to how `execute_txs_sequentially_inner` isolates per-item errors.
2. Distinguish a genuine `Invalid` classification (e.g., already consumed/cancelled) from a client/provider-level error (`L1EventsProviderClientError`). For the latter, retry or treat it as "unknown/pending" rather than immediately coercing to `Invalid` and failing the whole batch, to avoid node-local transient errors causing consensus-visible divergence.

### Proof of Concept
1. A proposer includes several transactions (mempool + one legitimate `L1Handler` tx) in a proposal at height `h`.
2. A validating node's `ValidateTransactionProvider::get_txs` receives the batch via `recv_many` and begins per-tx validation.
3. While validating the `L1Handler` transaction, the local `l1_events_provider_client.validate(...)` call transiently errors (e.g., local L1 events provider momentarily unavailable) — mapped to `Invalid(L1EventsProviderError)`. [2](#0-1) 
4. `get_txs` returns `Err(TransactionProviderError::L1HandlerTransactionValidationFailed)`, which propagates as `BlockBuilderError::GetTransactionError`/`FailOnError(L1HandlerTransactionValidationFailed)`, failing validation of the entire batch/proposal at this node — even though the other transactions in the same batch (and the L1Handler tx itself, in reality) were fine — while another validator whose L1 provider did not error accepts the same block, producing validator disagreement on the same proposal.

### Citations

**File:** crates/apollo_batcher/src/transaction_provider.rs (L195-223)
```rust
        let mut buffer = Vec::with_capacity(n_txs);
        self.tx_receiver.recv_many(&mut buffer, n_txs).await;

        for tx in &buffer {
            if let InternalConsensusTransaction::L1Handler(tx) = tx {
                let l1_validation_status = self
                    .l1_events_provider_client
                    .validate(tx.tx_hash, self.height)
                    .await
                    .inspect_err(|err| {
                        warn!(
                            "L1 provider error while validating L1 handler transaction: {:?}",
                            err
                        );
                        BATCHER_L1_EVENTS_PROVIDER_ERRORS.increment(1);
                    })
                    .unwrap_or(L1ValidationStatus::Invalid(
                        L1InvalidValidationStatus::L1EventsProviderError,
                    ));
                if let L1ValidationStatus::Invalid(validation_status) = l1_validation_status {
                    return Err(TransactionProviderError::L1HandlerTransactionValidationFailed {
                        tx_hash: tx.tx_hash,
                        validation_status,
                    });
                }
                continue;
            }
        }
        Ok(buffer)
```

**File:** crates/apollo_batcher/src/block_builder.rs (L85-102)
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
```

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L184-206)
```rust
    fn execute_txs_sequentially_inner(
        &mut self,
        txs: &[Transaction],
        execution_deadline: Option<Instant>,
    ) -> Vec<TransactionExecutorResult<TransactionExecutionOutput>> {
        let mut results = Vec::new();
        for tx in txs {
            if let Some(deadline) = execution_deadline {
                if Instant::now() > deadline {
                    log::debug!("Execution timed out.");
                    break;
                }
            }
            match self.execute(tx) {
                Ok((tx_execution_info, state_diff)) => {
                    results.push(Ok((tx_execution_info, state_diff)))
                }
                Err(TransactionExecutorError::BlockFull) => break,
                Err(error) => results.push(Err(error)),
            }
        }
        results
    }
```
