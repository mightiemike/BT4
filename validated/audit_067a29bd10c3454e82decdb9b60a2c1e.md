### Title
Attacker-triggerable panic in concurrent commit path halts block production - (File: crates/blockifier/src/concurrency/worker_logic.rs)

### Summary
The concurrent transaction executor's commit path (`commit_tx`) calls `self.bouncer.lock().expect(...).try_update(...)` and then explicitly `panic!`s on any `TransactionExecutorError` other than `BlockFull`. `try_update` can return `TransactionExecutorError::StateError`, `TransactionExecutorError::TransactionExecutionError`, or `TransactionExecutorError::CompressionError`, all of which are reachable via state reads/compression performed on data derived from an attacker-controlled transaction's execution results (state diff keys, execution summary, builtins, resources). A single malformed/edge-case transaction can cause this non-`BlockFull` branch to panic, crashing the worker thread executing block building. [1](#0-0) [2](#0-1) 

### Finding Description
In `WorkerExecutor::commit_tx`, after execution succeeds, the bouncer is asked whether there is room for the transaction via `self.bouncer.lock().expect("Bouncer lock failed.").try_update(...)`. The result is matched: only `TransactionExecutorError::BlockFull` is handled gracefully (returns `CommitResult::NoRoomInBlock`); every other error variant (`StateError`, `TransactionExecutionError`, `CompressionError`) falls into the `_` branch, which explicitly calls `panic!("Bouncer update failed. {error:?}: {error}")`, with a TODO comment acknowledging this should be "propagated" instead of panicking. [1](#0-0) 

This directly matches the analog bug-class from the report: using panic-based error handling in a critical processing loop instead of returning/propagating the error, with no recovery mechanism around the call, so a panic in this worker thread (used for concurrent block building) aborts that thread's execution flow for the block, which is a form of partial system failure / DoS on block production reachable from unprivileged transaction content, since the bouncer computation (`try_update`) operates on data derived from the state diff and execution summary of the just-executed, attacker-submitted transaction. [2](#0-1) 

Additionally, other panics in the same commit function further widen this reachable surface: `tx_at` panics with `expect("Transaction missing")` and `get_n_txs` panics via `.lock().expect(...)`, and the bouncer mutex itself is accessed via `.lock().expect("Bouncer lock failed.")`, so a poisoned lock (e.g., a prior panic in another worker) cascades panics across the block-building pipeline. [3](#0-2) 

### Impact Explanation
A panic during `commit_tx`, invoked from the concurrent scheduler's commit loop (`commit_while_possible`), interrupts block construction for that worker. Given no visible panic-recovery/catch mechanism at this call site (consistent with the reported "no visible panic recovery mechanism"), this can stall or corrupt block-building state, potentially causing the sequencer to fail to close/produce a block — a network-availability impact ("unable to confirm new transactions") triggered purely by transaction content chosen by an ordinary user, not requiring any privileged role. [4](#0-3) 

### Likelihood Explanation
Likelihood depends on whether an attacker can actually drive `try_update` into a non-`BlockFull` error path (e.g., a `StateError` from a storage/class-hash lookup performed inside bouncer weight computation, or a `CompressionError` from state-diff compression) using only a submitted transaction's execution results. I was not able to fully trace every internal branch of `try_update` inside the available context (the function body of `bouncer.rs`'s `try_update` was only partially retrieved), so I cannot conclusively confirm which specific error variants are reachable from attacker-controlled transaction execution versus only from internal/engineering bugs. This should be treated as a probable-but-not-fully-verified path given tool/context limits.

### Recommendation
Replace the `panic!` in the `_` arm of the `bouncer_result` match in `commit_tx` with proper error propagation (return a `Result` from `commit_tx`/`commit_while_possible` and surface the error to the caller so the block can be closed gracefully or the transaction rejected), consistent with the TODO comment already present in the code. Audit remaining `.expect()`/`panic!` usages in the concurrent worker path (`tx_at`, `get_n_txs`, bouncer lock) for the same treatment, and add panic-catching (`std::panic::catch_unwind` or thread supervision) around per-transaction concurrent execution/commit units so a single malformed transaction cannot abort the whole block-building worker.

### Proof of Concept
Conceptual reproduction (exact trigger conditions for the non-`BlockFull` bouncer error were not fully confirmed within the available context):
1. Submit a transaction whose execution succeeds but whose resulting state diff / execution summary causes `Bouncer::try_update` to return `TransactionExecutorError::StateError` or `TransactionExecutorError::CompressionError` during block building's concurrent commit phase.
2. `commit_tx` receives this error in the `Err(error)` branch, falls into `_ => panic!("Bouncer update failed. {error:?}: {error}")`. [5](#0-4) 
3. The panic unwinds the worker thread executing block construction; absent any catch/recovery wrapper visible around this call path, block production for the affected block is disrupted.

### Citations

**File:** crates/blockifier/src/concurrency/worker_logic.rs (L199-207)
```rust
    /// Returns the transaction at the given index.
    /// Panics if the transaction does not exist.
    fn tx_at(&self, tx_index: TxIndex) -> Arc<Transaction> {
        self.txs.get(&tx_index).expect("Transaction missing").value().clone()
    }

    fn get_n_txs(&self) -> usize {
        *self.n_txs.lock().expect("Failed to lock n_txs")
    }
```

**File:** crates/blockifier/src/concurrency/worker_logic.rs (L209-228)
```rust
    fn commit_while_possible(&self) {
        if let Some(mut tx_committer) = self.scheduler.try_enter_commit_phase() {
            while let Some(tx_index) = tx_committer.try_commit() {
                let commit_result = self.commit_tx(tx_index).unwrap_or_else(|_| {
                    panic!("Commit transaction should not be called after clearing the state.");
                });
                match commit_result {
                    CommitResult::Success => {}
                    CommitResult::NoRoomInBlock => {
                        tx_committer.uncommit();
                        self.scheduler.halt();
                    }
                    CommitResult::ValidationFailed => {
                        tx_committer.uncommit();
                        return;
                    }
                }
            }
        }
    }
```

**File:** crates/blockifier/src/concurrency/worker_logic.rs (L347-365)
```rust
            // Ask the bouncer if there is room for the transaction in the block.
            let bouncer_result = self.bouncer.lock().expect("Bouncer lock failed.").try_update(
                &tx_versioned_state,
                &tx_state_changes_keys,
                &execution_summary,
                &tx_execution_info.summarize_builtins(),
                &tx_execution_info.receipt.resources,
                &self.block_context.versioned_constants,
                tx_execution_info.receipt.gas.l2_gas,
            );
            if let Err(error) = bouncer_result {
                match error {
                    TransactionExecutorError::BlockFull => return Ok(CommitResult::NoRoomInBlock),
                    _ => {
                        // TODO(Avi, 01/07/2024): Consider propagating the error.
                        panic!("Bouncer update failed. {error:?}: {error}");
                    }
                }
            }
```

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L36-46)
```rust
#[derive(Debug, Error)]
pub enum TransactionExecutorError {
    #[error("Transaction cannot be added to the current block, block capacity reached.")]
    BlockFull,
    #[error(transparent)]
    StateError(#[from] StateError),
    #[error(transparent)]
    TransactionExecutionError(#[from] TransactionExecutionError),
    #[error(transparent)]
    CompressionError(#[from] CompressionError),
}
```
