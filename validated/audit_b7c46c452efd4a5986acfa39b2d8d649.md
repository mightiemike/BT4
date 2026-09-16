### Title
Unsynchronized scheduler halt lets worker threads keep mutating `VersionedState` after `close_block`/`abort_block` consumes it - ([File: crates/blockifier/src/blockifier/concurrent_transaction_executor.rs])

### Summary
`ConcurrentTransactionExecutor::close_block` and `abort_block` call `Scheduler::halt()` and then immediately treat the chunk's shared state as safe to consume/reuse, without ever waiting for the worker threads that are still mid-execution to actually stop touching it. This is the same bug class as the reported PL011 issue: a "terminate" call (`dmaengine_terminate_all` / here `scheduler.halt()`) that only requests termination, but does not synchronize with (wait for) the in-flight callback (here, a worker thread's `execute()`/`apply_writes()`), before the caller frees/moves/reuses the underlying buffer.

### Finding Description
`WorkerPool`'s own doc comment explicitly documents the race precondition: "If an execution of a chunk is halted (`Scheduler::halt`), each thread will continue to run until finishing the current execution (excluding reruns), and then move to the next chunk" [1](#0-0) . `Scheduler::halt()` itself does nothing but flip an atomic flag; it performs no blocking wait for any thread to observe the flag and exit `execute()`: [2](#0-1) .

`ConcurrentTransactionExecutor::close_block` calls `worker_executor.scheduler.halt()` and, without waiting for the halt to actually be observed by all worker threads, immediately calls `commit_chunk_and_recover_block_state`, which drains/consumes the versioned state (`self.state.into_inner_state()...`) to build the finalized block state: [3](#0-2) 

Likewise `abort_block` only halts and returns, letting the caller move on (e.g., to `start_block` for the next height) while a worker thread may still be inside `execute(tx_index)`, mutating the shared `VersionedState` via `apply_writes`: [4](#0-3) [5](#0-4) 

By contrast, the only path in this file that is actually synchronized correctly is `WorkerPool::run_and_wait`, which calls `scheduler.wait_for_completion(target_n_txs)` *before* halting — proving the codebase authors know synchronization is required, but `close_block`/`abort_block` bypass it: [6](#0-5) 

This is directly analogous to the PL011 CVE: `dmaengine_terminate_all()` (async "stop" request, no wait) vs. `dmaengine_terminate_sync()` (blocks until the callback is guaranteed done) — here, `halt()` is the async "stop" and there is no equivalent `wait_for_completion`-then-`halt` sequencing on the `close_block`/`abort_block` path before the state buffer is reclaimed.

### Impact Explanation
If a worker thread is still executing/writing into the chunk's `VersionedState` (via `DashMap`-backed `VersionedStorage`) at the moment the sequencer's block-builder thread calls `commit_chunk_and_recover_block_state`/`into_inner_state` to finalize the block, the resulting recovered `CachedState` (and hence the block's state diff / committed root) can be built from a state that is concurrently being mutated by another thread. This can produce non-deterministic state diffs, silently missing or extra writes, or a corrupted final state depending on scheduling — leading to an honest-node divergence in the committed state root/block hash, since two honest sequencer instances hitting slightly different thread timing during the halt race could commit different state roots for the same set of transactions. Any transaction that fills the block/hits the bouncer limit or block deadline triggers `abort_block`/halt path, so this is reachable by an ordinary transaction sender simply by causing block-full or deadline conditions.

### Likelihood Explanation
The race window exists on every block close (`close_block` is called on every block) and on every abort (deadline exceeded or block full — both of which an attacker can trigger just by sending enough/expensive transactions to fill a chunk or block). No privileged access is required; a normal transaction sender submitting transactions that reach the bouncer limit or block deadline is enough to exercise the `abort_block`/`close_block` path while other worker threads in the pool are still finishing their current `execute()` call on the same `WorkerExecutor`. The race is timing-dependent (a worker thread must be caught mid-`execute()` exactly as the caller halts and immediately reclaims state), so it is not deterministically triggerable per single test run, but is systematically reachable under load, matching a "High" (not "Critical") severity similar to the source CVE's own 7.8 CVSS rating for a timing race.

### Recommendation
Before consuming/moving the shared `VersionedState` (i.e., before `commit_chunk_and_recover_block_state`/`into_inner_state` in `close_block`, and before allowing a subsequent `start_block`/`add_txs` to proceed after `abort_block`), synchronize with the worker pool the same way `run_and_wait` does — e.g., call `scheduler.wait_for_completion(...)` (or an equivalent blocking join on the specific `WorkerExecutor`, not just the atomic `done_marker`) so that all workers have provably exited `execute()`/`apply_writes()` on that chunk before the state is reclaimed. This mirrors switching from `dmaengine_terminate_all()` to `dmaengine_terminate_sync()` in the original report.

### Proof of Concept
1. Configure `n_workers > 1` in `WorkerPoolConfig` and start a block via `ConcurrentTransactionExecutor::start_block`.
2. Submit a chunk of transactions via `add_txs` such that the block becomes full mid-chunk (trigger via bouncer weights) or the execution deadline elapses while at least one worker thread is still inside `execute(tx_index)` for a transaction not yet reflected in `commit_index`.
3. Observe: `close_block`/`abort_block` calls `scheduler.halt()` and immediately proceeds to call `commit_chunk_and_recover_block_state` (or lets a subsequent block start reusing the pool) without calling `wait_for_completion`; the worker thread is still writing into `VersionedStorage` via `apply_writes` concurrently with `into_inner_state`/`into_initial_state` reading/moving the same structures — the docstring in `worker_pool.rs` itself confirms threads "continue to run until finishing the current execution" past the halt point.

Note: because this bug depends on precise thread-scheduling timing, and the exact interleaving/consequences on `DashMap`/`VersionedStorage` internal locking were not fully traced to a guaranteed panic-free memory-safety violation (Rust's own data-structure locking may serialize individual field accesses even if higher-level consistency is violated), I could not fully confirm from static analysis alone whether the outcome is limited to "wrong committed state diff" (already sufficient impact per the rules) or could also cause a panic/crash. A live/dynamic Devin session with the ability to run and stress-test the worker pool would be needed to conclusively demonstrate the state-diff corruption end-to-end.

### Citations

**File:** crates/blockifier/src/concurrency/worker_pool.rs (L14-17)
```rust
///
/// If an execution of a chunk is halted (`Scheduler::halt`), each thread will continue to run until
/// finishing the current execution (excluding reruns), and then move to the next chunk.
/// The transactions that were not fully executed by the time halt was called will be discarded.
```

**File:** crates/blockifier/src/concurrency/worker_pool.rs (L80-90)
```rust
    pub fn run_and_wait(&self, worker_executor: Arc<WorkerExecutor<S>>, target_n_txs: TxIndex) {
        self.run(worker_executor.clone());

        worker_executor.scheduler.wait_for_completion(target_n_txs);

        // Halt the scheduler to allow future blocks to start.
        // This is required since `wait_for_completion` can exit before the scheduler is done.
        worker_executor.scheduler.halt();

        self.check_panic();
    }
```

**File:** crates/blockifier/src/concurrency/scheduler.rs (L171-173)
```rust
    pub fn halt(&self) {
        self.done_marker.store(true, Ordering::Release);
    }
```

**File:** crates/blockifier/src/blockifier/concurrent_transaction_executor.rs (L124-146)
```rust
    pub fn close_block(
        &mut self,
        final_n_executed_txs: usize,
    ) -> TransactionExecutorResult<BlockExecutionSummary> {
        log::info!("Worker executor: Closing block.");
        let worker_executor = &self.worker_executor;
        worker_executor.scheduler.halt();

        let n_committed_txs = worker_executor.scheduler.get_n_committed_txs();
        assert!(
            final_n_executed_txs <= n_committed_txs,
            "Close block requested with {final_n_executed_txs} transactions, but only \
             {n_committed_txs} transactions were committed."
        );

        let mut state_after_block =
            worker_executor.commit_chunk_and_recover_block_state(final_n_executed_txs);
        finalize_block(
            &worker_executor.bouncer,
            &mut state_after_block,
            &self.worker_executor.block_context,
        )
    }
```

**File:** crates/blockifier/src/blockifier/concurrent_transaction_executor.rs (L154-158)
```rust
    /// Halts the scheduler, to allow the worker threads to continue to the next block.
    pub fn abort_block(&mut self) {
        log::info!("Worker executor: Aborting block.");
        self.worker_executor.scheduler.halt();
    }
```

**File:** crates/blockifier/src/concurrency/versioned_state.rs (L138-168)
```rust
    fn apply_writes(
        &mut self,
        tx_index: TxIndex,
        writes: &StateMaps,
        class_hash_to_class: &ContractClassMapping,
    ) {
        for (&key, &value) in &writes.storage {
            self.storage.write(tx_index, key, value);
        }
        for (&key, &value) in &writes.nonces {
            self.nonces.write(tx_index, key, value);
        }
        for (&key, &value) in &writes.class_hashes {
            self.class_hashes.write(tx_index, key, value);
        }
        for (&key, &value) in &writes.compiled_class_hashes {
            self.compiled_class_hashes.write(tx_index, key, value);
        }
        for (&key, value) in class_hash_to_class {
            self.compiled_contract_classes.write(tx_index, key, value.clone());
        }
        for (&key, &value) in &writes.declared_contracts {
            self.declared_contracts.write(tx_index, key, value);
            assert_eq!(
                value,
                self.compiled_contract_classes.read(tx_index, key).is_some(),
                "The declared contracts mapping should match the compiled contract classes \
                 mapping."
            );
        }
    }
```
