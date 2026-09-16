## Title
Single worker-thread panic permanently disables block proposal and validation for the lifetime of the batcher process — ([File: crates/blockifier/src/concurrency/worker_pool.rs])

### Summary
The `WorkerPool` used to execute transactions concurrently is created **once** at batcher start-up and reused for every subsequent block (both propose and validate flows). It tracks thread health with a single, never-reset `AtomicBool` (`a_thread_panicked`). If any worker thread panics while executing a *single* transaction, that flag is permanently set to `true` and is never cleared. From that moment on, `WorkerPool::check_panic()` — which is invoked on every block-building iteration — deterministically re-panics, so **every future block, for the rest of the process's life, fails to build or validate**. This mirrors the Reserve Protocol bug class: one bad, unexpectedly-behaving input (there, a single collateral asset; here, a single transaction that trips a panic in one execution thread) permanently corrupts a long-lived, globally shared piece of state and renders the *entire* system unusable, with no in-process recovery path — only a manual restart, analogous to Reserve needing governance intervention.

### Finding Description
`WorkerPool` is constructed exactly once, in `create_batcher`, and handed to `BlockBuilderFactory`, which reuses the very same `Arc<WorkerPool<...>>` for every block built afterwards: [1](#0-0) 

Each call to build or validate a block calls `preprocess_and_create_transaction_executor`, which passes `self.worker_pool.clone()` — the same pool instance — into `ConcurrentTransactionExecutor::start_block`: [2](#0-1) 

Inside `WorkerPool`, thread health is tracked by a single shared flag that is set but never cleared: [3](#0-2) [4](#0-3) 

When a worker thread panics while executing/committing a transaction, `_run_executor` catches the panic, sets `a_thread_panicked = true`, halts the current chunk's scheduler, and then re-raises the panic on that thread: [5](#0-4) 

There is no code path anywhere in the repository that resets `a_thread_panicked` back to `false` (verified: all references to the field live only in `worker_pool.rs`, none of which write `false`). Since the very same `WorkerPool` (and therefore the very same flag) is reused for all subsequent blocks, `ConcurrentTransactionExecutor::get_new_results` — called on every polling iteration of every future block — will hit: [6](#0-5) 

and unconditionally panic again via `check_panic()`, forever.

Multiple panic sites inside the concurrent execution path are reachable from ordinary (non-privileged) transaction processing, e.g. the bouncer-update commit path panics on any non-`BlockFull` error, and a defensive `checked_add(...).expect(...)` panics on arithmetic overflow when accumulating block-wide resource weights: [7](#0-6) [8](#0-7) 

Any single transaction that reaches one of these (or any other) panic/`unwrap()`/`expect()`/`assert!` inside the code executed on a worker thread (`WorkerExecutor::run` → `execute`/`validate`/`commit_tx`) is sufficient to trip this permanent kill-switch — no attacker privilege beyond submitting an ordinary transaction that reaches the gateway/mempool/executor is required.

### Impact Explanation
Once tripped, the sequencer node can never again successfully propose or validate a block through this batcher instance: `build_block_inner`'s polling loop calls `handle_executed_txs` → `get_new_results` → `check_panic()` on every iteration of every future block, so it panics immediately on the very first poll of the very next block. Because propose and validate both route through the same shared `worker_pool`, this affects both roles equally. Depending on how the panic unwinds through the async task hierarchy, this results in a network unable to confirm new transactions (all further block proposals/validations by that node fail) — one of the accepted impact categories — and requires an out-of-band process restart to recover (no governance/self-healing mechanism exists in-process), directly paralleling Reserve's "permanently insolvent/unusable until governance intervenes."

### Likelihood Explanation
The likelihood hinges only on the existence of *any* reachable panic/`unwrap`/`expect`/arithmetic-overflow inside the concurrent execution, validation, or commit path for a transaction that got past gateway/mempool admission — a broad attack surface given the number of `panic!`/`expect()`/`unwrap()` calls identified in `worker_logic.rs`, `bouncer.rs`, and `cached_state.rs` that are executed per-transaction on worker threads. A single crafted transaction is sufficient; no repeated interaction, elevated privileges, or special node/operator behavior is needed.

### Recommendation
- Do not let a single worker-thread panic permanently disable an entire long-lived `WorkerPool`. Either recreate/reset the pool (and its `a_thread_panicked` flag) at the start of every new block, or convert the "poison" into a per-block-scoped error (`Result`) rather than a process/pool-lifetime flag.
- Audit and remove/replace `panic!`/`unwrap()`/`expect()` calls in `worker_logic.rs` and `bouncer.rs` that are reachable from attacker-controlled transaction content (e.g., the `commit_tx` "Bouncer update failed" panic, and the `checked_add(...).expect(...)` overflow panic) with graceful error propagation that only fails the offending transaction/block, not all future blocks.
- Add a supervisory mechanism that detects a panicked `WorkerPool` and transparently rebuilds it for subsequent proposals/validations instead of requiring a full process restart.

### Proof of Concept
1. A transaction is admitted by the gateway/mempool and reaches block building.
2. During concurrent execution (`WorkerExecutor::run` → `execute`/`commit_tx`), the transaction's execution/commit path hits one of the reachable panic sites (e.g., `bouncer.try_update` returning a non-`BlockFull` error inside `commit_tx`, triggering `panic!("Bouncer update failed. ...")` at [9](#0-8) ).
3. `WorkerThread::_run_executor` catches the panic via `panic::catch_unwind`, sets `a_thread_panicked.store(true, Ordering::Release)` (worker_pool.rs:154), halts the scheduler, and resumes the unwind.
4. The current block build fails; but crucially, the `Arc<WorkerPool>` created once in `create_batcher` (batcher.rs:1742) is reused for the *next* proposal/validation as well (block_builder.rs:806).
5. On the next block, the very first call to `get_new_results()` invokes `self.worker_pool.check_panic()` (concurrent_transaction_executor.rs:97), which reads `a_thread_panicked == true` and immediately panics again (worker_pool.rs:93-95) — before any transaction of the new block is even processed.
6. This repeats for every subsequent block indefinitely, since nothing in the codebase ever resets `a_thread_panicked` to `false`.

### Citations

**File:** crates/apollo_batcher/src/batcher.rs (L1741-1757)
```rust
    let execute_config = &config.static_config.block_builder_config.execute_config;
    let worker_pool = Arc::new(WorkerPool::start(execute_config));
    let pre_confirmed_block_writer_factory = Box::new(PreconfirmedBlockWriterFactory {
        config: config.static_config.pre_confirmed_block_writer_config,
        cende_client: pre_confirmed_cende_client,
    });
    // Block production and view calls share one class cache.
    let contract_class_manager =
        ContractClassManager::start(config.static_config.contract_class_manager_config.clone());
    let block_builder_factory = Box::new(BlockBuilderFactory {
        block_builder_config: config.static_config.block_builder_config.clone(),
        storage_reader: storage_reader.clone(),
        contract_class_manager: contract_class_manager.clone(),
        class_manager_client: class_manager_client.clone(),
        proof_manager_client,
        worker_pool,
    });
```

**File:** crates/apollo_batcher/src/block_builder.rs (L795-811)
```rust
        let state_reader = StateReaderAndContractManager::new_with_native_classes_whitelist(
            apollo_reader,
            self.contract_class_manager.clone(),
            native_classes_whitelist,
            Some(BATCHER_CLASS_CACHE_METRICS),
        );

        let executor = ConcurrentTransactionExecutor::start_block(
            state_reader,
            block_context,
            block_metadata.retrospective_block_hash,
            self.worker_pool.clone(),
            None,
        )?;

        Ok(executor)
    }
```

**File:** crates/blockifier/src/concurrency/worker_pool.rs (L18-24)
```rust
#[derive(Debug)]
pub struct WorkerPool<S: StateReader> {
    senders: Vec<mpsc::Sender<Option<Arc<WorkerExecutor<S>>>>>,
    handlers: Vec<std::thread::JoinHandle<()>>,
    /// Whether one of the threads panicked.
    a_thread_panicked: Arc<AtomicBool>,
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

**File:** crates/blockifier/src/blockifier/concurrent_transaction_executor.rs (L93-100)
```rust
    pub fn get_new_results(
        &mut self,
    ) -> Vec<TransactionExecutorResult<TransactionExecutionOutput>> {
        let res = self.worker_executor.extract_execution_outputs(self.n_output_txs);
        self.worker_pool.check_panic();
        self.n_output_txs += res.len();
        res
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

**File:** crates/blockifier/src/bouncer.rs (L664-691)
```rust
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
