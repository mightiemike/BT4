### Title
Any unprivileged transaction that trips the Bouncer with a non-`BlockFull` error crashes the whole worker thread pool during concurrent block building - (File: `crates/blockifier/src/concurrency/worker_logic.rs`)

### Summary
The external ERC-777 report describes a class of bug where a single, attacker-influenced participant in a batch operation can make the entire batch abort, denying service/fees to everyone else in that batch. The `sequencer--002` codebase has a structurally identical pattern in the concurrent transaction-execution/commit path: a single transaction's `Bouncer::try_update` result that is an error *other than* `BlockFull` causes a `panic!` inside `WorkerExecutor::commit_tx`, which propagates out of the worker thread and aborts/poisons the whole chunk's execution, rather than being contained to that one transaction.

### Finding Description
In `WorkerExecutor::commit_tx`, after a transaction executes successfully, the bouncer is asked whether there is room for it in the block: [1](#0-0) 
If `try_update` fails with `TransactionExecutorError::BlockFull` this is handled gracefully (`CommitResult::NoRoomInBlock`), but for **any other error variant** the code executes `panic!("Bouncer update failed. {error:?}: {error}")`. `TransactionExecutorError` also has `StateError` and `TransactionExecutionError`/`CompressionError` variants that can be produced deep inside `get_tx_weights` while computing marginal weights for state reads / class-hash bookkeeping for a specific, attacker-controlled transaction (e.g., contract/class-related reads or state-diff computations tied to that transaction's execution). Because commit runs inside a dedicated worker thread spawned by `WorkerPool`, this panic is caught by `panic::catch_unwind` in `WorkerThread::_run_executor`, but the handler explicitly re-panics via `panic::resume_unwind(err)` after flagging `a_thread_panicked` and halting the scheduler: [2](#0-1) 
The re-thrown panic terminates the worker thread. `WorkerPool::join()` then calls `handler.join().expect("Failed to join thread.")`, which itself panics on the calling (batcher/executor) thread once a worker thread has died: [3](#0-2) 
This means one single transaction that triggers a non-`BlockFull` bouncer error is sufficient to bring down the entire concurrent execution chunk/thread pool used for block building — this is invoked from `TransactionExecutor::execute_chunk`, called for every chunk of transactions pulled from the mempool during proposal or validation: [4](#0-3) 

This is structurally analogous to the ERC-777 report: a single participant's action (there, a reverting hook; here, a transaction whose bouncer bookkeeping errors) is not isolated and instead aborts processing for the whole batch/pool of unrelated transactions.

### Impact Explanation
If reachable by an ordinary transaction, this would be a serious availability bug: any Starknet node acting as sequencer/validator running block building in concurrent mode could have its batcher process crash or its worker pool become unusable when processing a maliciously-crafted transaction, halting new block production (a "network unable to confirm new transactions" condition per the acceptance criteria) until the operator manually intervenes/restarts.

### Likelihood Explanation
This is **not confirmed as reachable from an ordinary, unprivileged transaction** with my available tooling. `TransactionExecutorError::StateError`/other non-`BlockFull` variants surfacing from `Bouncer::try_update`/`get_tx_weights` appear (based on comments such as `// TODO(Avi, 01/07/2024): Consider propagating the error.`) to represent conditions the developers consider "should not normally happen" (e.g., underlying storage/state-reader failures), rather than something directly triggerable by transaction content alone. I could not fully trace `get_tx_weights` (in `crates/blockifier/src/bouncer.rs`) to determine whether any of its internal state reads (e.g., resolving a newly-declared/executed class for CASM-hash computation gas accounting) can be forced to fail purely via attacker-supplied calldata/contract code, as opposed to only failing due to storage corruption or a sequencer bug. Because of this gap, I cannot assert with confidence that an unprivileged sender can force this specific error path; the report should be treated as identifying a fragile fail-fast pattern (crash-on-panic instead of contained-error) rather than a proven, concretely exploitable DoS.

### Recommendation
- Do not `panic!` on non-`BlockFull` `TransactionExecutorError`s in `WorkerExecutor::commit_tx`; propagate the error so that only the offending transaction is rejected/excluded from the block, without terminating the worker thread or thread pool.
- Audit `Bouncer::try_update` / `get_tx_weights` (`crates/blockifier/src/bouncer.rs`) to confirm whether any state reads or arithmetic there (including the `checked_add(...).expect(&err_msg)` overflow check at `crates/blockifier/src/bouncer.rs:669-670`) can be triggered by attacker-controlled transaction content, and harden those paths to return recoverable errors instead of panicking.
- Consider isolating worker-thread panics so that a single panicking transaction only fails that transaction/chunk rather than requiring the entire `WorkerPool` to be torn down (currently `join()` panics the caller on any worker panic).

### Proof of Concept
Not established. A concrete PoC would require constructing a transaction whose `Bouncer::try_update` call returns `TransactionExecutorError::StateError` (or another non-`BlockFull` variant) during `WorkerExecutor::commit_tx`, which was not possible to confirm from the available code paths within this investigation. Further analysis of `get_tx_weights` and the underlying state-reader error surfaces in `crates/blockifier/src/bouncer.rs` would be needed to build or rule out such a PoC.

### Citations

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

**File:** crates/blockifier/src/concurrency/worker_pool.rs (L98-106)
```rust
    pub fn join(self) {
        // Send None to all senders to stop the threads.
        for sender in self.senders {
            sender.send(None).expect("Failed to signal worker thread to stop.");
        }
        for handler in self.handlers {
            handler.join().expect("Failed to join thread.");
        }
    }
```

**File:** crates/blockifier/src/concurrency/worker_pool.rs (L138-166)
```rust
    /// Runs a single worker executor.
    fn _run_executor(&self, worker_executor: &WorkerExecutor<S>) {
        if self.a_thread_panicked.load(Ordering::Acquire) {
            panic!("Another thread panicked. Aborting.");
        }

        // Making sure that the program will abort if a panic occurred while halting
        // the scheduler.
        let abort_guard = AbortIfPanic;
        // If a panic is not handled or the handling logic itself panics, then we
        // abort the program.
        let res = panic::catch_unwind(panic::AssertUnwindSafe(|| {
            worker_executor.run();
        }));
        if let Err(err) = res {
            // First, set the panic flag. This must be done before halting the scheduler.
            self.a_thread_panicked.store(true, Ordering::Release);

            // If the program panics here, the abort guard will exit the program.
            // In this case, no panic message will be logged. Add the cargo flag
            // --nocapture to log the panic message.

            worker_executor.scheduler.halt();
            abort_guard.release();
            panic::resume_unwind(err);
        }

        abort_guard.release();
    }
```

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L397-435)
```rust
    fn execute_chunk(
        &mut self,
        chunk: &[Transaction],
        execution_deadline: Option<Instant>,
    ) -> Vec<TransactionExecutorResult<TransactionExecutionOutput>>
    where
        S: 'static,
    {
        let block_state = self.block_state.take().expect("The block state should be `Some`.");

        let worker_executor = Arc::new(WorkerExecutor::initialize(
            block_state,
            // We need to clone the transactions so that ownership can be shared between threads,
            // that will live longer than the current function.
            // TODO(lior): Move the transactions instead of cloning them.
            chunk.to_vec(),
            self.block_context.clone(),
            self.bouncer.clone(),
            execution_deadline,
        ));

        if let Some(worker_pool) = &mut self.worker_pool {
            worker_pool.run_and_wait(worker_executor.clone(), chunk.len());
        } else {
            // If a pool is not given, create a new pool and wait for it to finish.
            let worker_pool = WorkerPool::start(&self.config.get_worker_pool_config());
            worker_pool.run_and_wait(worker_executor.clone(), chunk.len());
            worker_pool.join();
        }

        let tx_execution_results = worker_executor.extract_execution_outputs(0);
        let n_committed_txs = tx_execution_results.len();

        let block_state_after_commit =
            worker_executor.commit_chunk_and_recover_block_state(n_committed_txs);
        self.block_state.replace(block_state_after_commit);

        tx_execution_results
    }
```
