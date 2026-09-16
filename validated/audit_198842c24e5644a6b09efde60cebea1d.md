### Title
Any non-`BlockFull` bouncer error during concurrent commit permanently poisons the shared `WorkerPool`, halting all future block building - (File: `crates/blockifier/src/concurrency/worker_logic.rs`, `crates/blockifier/src/concurrency/worker_pool.rs`)

### Summary
In the concurrent transaction-execution path, `WorkerExecutor::commit_tx` treats every bouncer error that is not `TransactionExecutorError::BlockFull` as an unrecoverable condition and calls `panic!(...)` directly inside a worker thread. `WorkerPool`'s panic handling sets a permanent `AtomicBool` (`a_thread_panicked`) and re-raises the panic, and this flag is checked on every subsequent invocation of the pool. Because the `WorkerPool` is a long-lived, shared resource reused across blocks (created once and passed via `Arc` into `ConcurrentTransactionExecutor::start_block` for every new block), a single transaction that drives the bouncer into a non-`BlockFull` error path permanently poisons block production for the whole node - all subsequent blocks fail as soon as they touch the pool.

### Finding Description
`Bouncer::try_update` calls `get_tx_weights`, which itself calls `map_class_hash_to_casm_hash_computation_resources` and `CasmHashMigrationData::from_state`, both of which read from the state reader and can bubble up a `TransactionExecutionError` via `?` (e.g., state-read/consistency errors) in addition to the expected `BlockFull` condition: [1](#0-0) 

In the concurrent commit path, `WorkerExecutor::commit_tx` explicitly branches on the bouncer result: `BlockFull` is handled gracefully (`CommitResult::NoRoomInBlock`), but any other error is turned into a hard `panic!`, with a comment acknowledging this is a known gap ("Consider propagating the error"): [2](#0-1) 

That panic occurs on a worker thread managed by `WorkerPool::_run_executor`, which catches the panic only to record it globally and then **re-raises it**, and the shared `a_thread_panicked` flag is never cleared once set: [3](#0-2) 

Every other worker thread checks this flag at the top of `_run_executor` and immediately panics too ("Another thread panicked. Aborting."), and every caller of `get_new_results`/`run_and_wait` invokes `check_panic()`, which panics the calling (batcher) thread as well: [4](#0-3) 

Critically, this `WorkerPool` is not a per-block, disposable object — it is created once (e.g. at node startup) and reused across blocks via `Arc` cloning: [5](#0-4) [6](#0-5) 

So one transaction that reaches the panicking branch poisons the pool for the remainder of the process's lifetime — all future block-building/validation attempts on any subsequent height will hit the poisoned flag and panic immediately.

### Impact Explanation
This matches the report's bug class: a bounded action by a single unprivileged party (one transaction) permanently disables shared block-processing infrastructure, causing the sequencer to be unable to confirm any further transactions/blocks — a denial-of-service on block production analogous to the Carousel `mintRollovers` DoS, where one poisoned entry blocks the shared batch process for everyone.

### Likelihood Explanation
The exact state condition that forces `get_tx_weights` to return a non-`BlockFull` error (rather than the expected paths) was not fully confirmed via static analysis within the exploration budget — it would require a state-reader failure or class-hash/casm-hash inconsistency during the bouncer weight computation for a class that was executed within the transaction being committed. Given that `map_class_hash_to_casm_hash_computation_resources` and `CasmHashMigrationData::from_state` perform state lookups keyed by class hashes touched mid-execution, some legitimate error path here (e.g. a transient state-read/backend error, or a migration-data inconsistency) is plausible, but confirming a concrete transaction/class construction that reliably triggers it requires further investigation (ideally with a running node/test harness), which is out of scope for this static review.

### Recommendation
Do not `panic!` on non-`BlockFull` bouncer errors inside `commit_tx`; propagate the error through `CommitResult` (or an equivalent `Result`) so it can cause only the *current block* to abort (as already done for other per-transaction/per-block errors), without poisoning the long-lived, cross-block `WorkerPool`. Additionally, consider making `a_thread_panicked` recoverable/resettable per block (e.g., only fail the current block/pool instance) rather than a permanent, process-lifetime flag shared across all future blocks.

### Proof of Concept
Not independently reproduced; the mechanism is demonstrated by the code paths cited above (`commit_tx`'s `panic!` on non-`BlockFull` errors, and `WorkerPool`'s permanent `a_thread_panicked` flag that poisons all subsequent block-building calls). A full PoC would require constructing a transaction whose executed class hashes cause `get_tx_weights` (via `map_class_hash_to_casm_hash_computation_resources` or `CasmHashMigrationData::from_state`) to return an `Err` variant other than the size/capacity check, which was not confirmed within the available exploration.

### Citations

**File:** crates/blockifier/src/bouncer.rs (L650-691)
```rust
        let tx_weights = get_tx_weights(
            state_reader,
            &marginal_executed_class_hashes,
            n_marginal_visited_storage_entries,
            tx_resources,
            &marginal_state_changes_keys,
            versioned_constants,
            tx_builtin_counters,
            &self.bouncer_config,
            receipt_l2_gas,
        )?;

        let tx_bouncer_weights = tx_weights.bouncer_weights;

        // Check if the transaction can fit the current block available capacity.
        let err_msg = format!(
            "Addition overflow. Transaction weights: {tx_bouncer_weights:?}, block weights: {:?}.",
            self.get_bouncer_weights()
        );
        let next_accumulated_weights =
            self.get_bouncer_weights().checked_add(tx_bouncer_weights).expect(&err_msg);
        if !self.bouncer_config.has_room(next_accumulated_weights) {
            let exceeded_weights =
                self.bouncer_config.get_exceeded_weights(next_accumulated_weights);
            log::debug!(
                "Transaction cannot be added to the current block, block capacity reached; \
                 transaction weights: {:?}, block weights: {:?}. Block max capacity reached on \
                 fields: {}",
                tx_weights.bouncer_weights,
                self.get_bouncer_weights(),
                exceeded_weights
            );
            // Record the block-full metric only once per block. Later candidate txs that also do
            // not fit (subsequent chunks / executor invocations share this bouncer) would otherwise
            // inflate the counter into a per-rejected-tx count instead of a per-block count.
            if !self.block_full_recorded {
                record_exceeded_bouncer_resources(&exceeded_weights);
                self.block_full_recorded = true;
            }
            Err(TransactionExecutorError::BlockFull)?
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

**File:** crates/blockifier/src/concurrency/worker_pool.rs (L92-96)
```rust
    pub fn check_panic(&self) {
        if self.a_thread_panicked.load(Ordering::Acquire) {
            panic!("One of the threads panicked.");
        }
    }
```

**File:** crates/blockifier/src/concurrency/worker_pool.rs (L139-167)
```rust
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
}
```

**File:** crates/apollo_batcher/src/block_builder.rs (L802-808)
```rust
        let executor = ConcurrentTransactionExecutor::start_block(
            state_reader,
            block_context,
            block_metadata.retrospective_block_hash,
            self.worker_pool.clone(),
            None,
        )?;
```

**File:** crates/blockifier/src/blockifier/concurrent_transaction_executor.rs (L36-61)
```rust
    pub fn start_block(
        initial_state_reader: S,
        block_context: BlockContext,
        old_block_number_and_hash: Option<BlockHashAndNumber>,
        worker_pool: Arc<WorkerPool<CachedState<S>>>,
        block_deadline: Option<Instant>,
    ) -> StateResult<Self> {
        let mut block_state = CachedState::new(initial_state_reader);
        pre_process_block(
            &mut block_state,
            old_block_number_and_hash,
            block_context.block_info().block_number,
            &block_context.versioned_constants.os_constants,
        )?;

        let bouncer_config = block_context.bouncer_config.clone();
        let worker_executor = Arc::new(WorkerExecutor::initialize(
            block_state,
            vec![],
            block_context.into(),
            Mutex::new(Bouncer::new(bouncer_config)).into(),
            block_deadline,
        ));
        worker_pool.run(worker_executor.clone());

        Ok(Self { worker_executor, worker_pool: worker_pool.clone(), n_output_txs: 0 })
```
