Based on my research, the strongest reachable analog in this codebase is not a header-encoding panic (there is no WSGI-style header path in the sequencer), but a structurally identical bug class: **an untrusted-transaction-triggerable `panic!()` inside the concurrent block-execution worker pool that permanently poisons a long-lived, shared "panic" flag, disabling block production for all future blocks** — the same "one bad input turns into an unrecoverable worker failure" pattern as the Granian issue, except here the blast radius is the whole sequencer's block-building capability rather than one HTTP worker. [1](#0-0) 

### Title
Unbounded panic in `WorkerExecutor::commit_tx` permanently poisons the shared `WorkerPool`, halting all future block building - ([File: crates/blockifier/src/concurrency/worker_logic.rs])

### Summary
`WorkerPool` is a long-lived object, created once and reused across many blocks by the batcher [2](#0-1) . It tracks a single `Arc<AtomicBool>` called `a_thread_panicked` that is only ever set to `true`, and is never reset anywhere in the codebase. Any panic raised while executing/committing a transaction inside a worker thread sets this flag permanently and is then propagated as a resumed panic [3](#0-2) . Once set, every subsequent worker thread checks the flag at the start of `_run_executor` and immediately panics with `"Another thread panicked. Aborting."` [4](#0-3) , so every future block submitted to this pool fails.

### Finding Description
Inside `WorkerExecutor::commit_tx`, when a transaction executes successfully, the bouncer is asked whether the transaction fits in the block via `try_update`. Any bouncer error other than `TransactionExecutorError::BlockFull` results in an explicit `panic!("Bouncer update failed. {error:?}: {error}")`: [5](#0-4) 

This panic occurs inside `worker_executor.run()`, which is executed under `panic::catch_unwind` in the worker-pool thread loop, so the immediate panic does not abort the OS process — but the catch_unwind handler unconditionally sets `a_thread_panicked = true` before halting the scheduler and re-raising the panic: [6](#0-5) 

Because `WorkerPool` (and its `a_thread_panicked` flag) is shared across the lifetime of the batcher/proposer rather than being re-created per block, and there is no code path anywhere that resets `a_thread_panicked` back to `false`, this is a one-way trip: the very next block submitted to the pool will have every worker thread panic immediately at the top of `_run_executor` before doing any useful work: [7](#0-6) 

This mirrors the Granian bug class exactly: a code path reachable through normal (if unusual) transaction processing calls an unconditional panic on an error variant that "should not happen," and — because of surrounding architecture (persistent worker pool / `panic=abort` release profile in Granian's case) — a single bad input converts a should-be-handled error into total, non-recoverable service failure rather than a request/transaction-scoped rejection.

### Impact Explanation
If any transaction accepted by the block-building pipeline can cause `Bouncer::try_update` to return an error variant other than `BlockFull` (e.g., a state-read or resource-accounting error surfaced from `try_update`), the resulting `panic!` permanently disables the sequencer's concurrent transaction executor. All subsequent block-building attempts on that `WorkerPool` will immediately fail for every worker thread, meaning the node can no longer produce or validate blocks — i.e., "a network unable to confirm new transactions," which is one of the explicitly accepted impact categories.

### Likelihood Explanation
Likelihood is uncertain and I could not fully confirm it within the available search budget: I was unable to enumerate every non-`BlockFull` error variant `Bouncer::try_update` can return, nor confirm whether any of those variants are reachable from a single, otherwise-valid, unprivileged transaction (as opposed to only from internal/state-corruption bugs). The finding is architecturally sound (persistent flag, no reset, panic on non-`BlockFull` bouncer errors) but I could not verify a concrete, attacker-controlled input that forces `try_update` down the panicking branch. This should be validated by inspecting `crates/blockifier/src/bouncer.rs`'s `try_update`/`TransactionExecutorError` variants and whether any of them can be triggered by contract execution characteristics (e.g., resource/weight computation edge cases) controllable by a transaction sender.

### Recommendation
- Do not use bare `panic!()` for unexpected bouncer errors in `commit_tx`; propagate the error through `CommitResult` (an `Err` variant) so it can be handled per-transaction (e.g., reject the transaction, or fail only the current block) instead of poisoning the shared worker pool.
- Reset `a_thread_panicked` (and any other cross-block-persistent flags in `WorkerPool`) at the start of each new block/`run()` invocation, or recreate the pool per block, so a single failure cannot permanently disable future block building.
- Audit all other `panic!`/`.unwrap()`/`.expect()` calls reachable from transaction execution inside `WorkerExecutor`/`WorkerPool` for the same "persistent poisoning" hazard.

### Proof of Concept
Not fully constructible from static analysis alone: it requires identifying a concrete transaction (or transaction sequence) that drives `Bouncer::try_update` into a non-`BlockFull` error branch during normal execution. Conceptually:
1. Submit a transaction whose resource/weight bookkeeping causes `Bouncer::try_update` to return an error other than `TransactionExecutorError::BlockFull` (exact trigger unverified).
2. `commit_tx` panics: `panic!("Bouncer update failed. {error:?}: {error}")`.
3. `worker_pool.rs::_run_executor` catches the panic, sets `a_thread_panicked = true`, halts the scheduler, and re-raises.
4. Any subsequent block submitted to the same `WorkerPool` immediately panics for every worker thread at the `a_thread_panicked` check, since the flag is never reset.

### Citations

**File:** crates/blockifier/src/concurrency/worker_pool.rs (L19-24)
```rust
pub struct WorkerPool<S: StateReader> {
    senders: Vec<mpsc::Sender<Option<Arc<WorkerExecutor<S>>>>>,
    handlers: Vec<std::thread::JoinHandle<()>>,
    /// Whether one of the threads panicked.
    a_thread_panicked: Arc<AtomicBool>,
}
```

**File:** crates/blockifier/src/concurrency/worker_pool.rs (L26-72)
```rust
impl<S: StateReader + Send + 'static> WorkerPool<S> {
    /// Creates a new WorkerPool with the given stack size and concurrency configuration.
    pub fn start(config: &WorkerPoolConfig) -> Self {
        // Initialize the channels.
        let a_thread_panicked = Arc::new(AtomicBool::new(false));
        let mut senders = Vec::<mpsc::Sender<Option<Arc<WorkerExecutor<S>>>>>::new();
        let mut receivers = Vec::<mpsc::Receiver<Option<Arc<WorkerExecutor<S>>>>>::new();
        for _ in 0..config.n_workers {
            let (sender, receiver) = mpsc::channel();
            senders.push(sender);
            receivers.push(receiver);
        }

        let stack_size = config.stack_size;

        // Run the threads.
        let handlers = receivers
            .into_iter()
            .enumerate()
            .map(|(thread_id, receiver)| {
                let mut thread_builder = std::thread::Builder::new();
                // When running Cairo natively, the real stack is used and could get overflowed
                // (unlike the VM where the stack is simulated in the heap as a memory segment).
                //
                // We pre-allocate the stack here, and not during Native execution (not trivial), so
                // it needs to be big enough ahead.
                // However, making it very big is wasteful (especially with multi-threading).
                // So, the stack size should support calls with a reasonable gas limit, for
                // extremely deep recursions to reach out-of-gas before hitting the
                // bottom of the recursion.
                //
                // The gas upper bound is MAX_POSSIBLE_SIERRA_GAS, and sequencers must not raise it
                // without adjusting the stack size.
                thread_builder = thread_builder.stack_size(stack_size);
                let worker_thread = WorkerThread {
                    a_thread_panicked: a_thread_panicked.clone(),
                    receiver,
                    thread_id,
                };
                thread_builder
                    .spawn(move || worker_thread.run_thread())
                    .expect("Failed to spawn thread.")
            })
            .collect();

        WorkerPool { senders, handlers, a_thread_panicked }
    }
```

**File:** crates/blockifier/src/concurrency/worker_pool.rs (L115-166)
```rust
impl<S: StateReader> WorkerThread<S> {
    /// Fetches worker executors from the channel, until None is received.
    fn run_thread(&self) {
        let mut i = 0;
        while let Some(worker_executor) =
            self.receiver.recv().expect("Failed to receive worker executor.")
        {
            let block_number = worker_executor.block_context.block_info.block_number;
            log::debug!(
                "Worker pool (thread {}) starting worker #{} (block number {block_number})",
                self.thread_id,
                i,
            );
            self._run_executor(&*worker_executor);
            log::debug!(
                "Worker pool (thread {}) worker done #{} (block number {block_number})",
                self.thread_id,
                i,
            );
            i += 1;
        }
    }

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
