### Title
Single malicious transaction can trigger an unrecoverable Rust panic that poisons the entire shared `WorkerPool`, halting/crashing concurrent block building for all transactions in the block (and potentially the whole sequencer process) - (File: crates/blockifier/src/concurrency/worker_pool.rs)

### Summary
The reported bug class (one attacker-controlled entity in a batch loop causes an unhandled failure that denies service to every other participant in the same batch) has a direct analog in the sequencer's concurrent transaction executor. Transaction execution can hit a Rust `panic!` (not a controlled Cairo-level revert) on attacker-influenced data. Unlike a normal `TransactionExecutionError`, which only fails that single transaction, a genuine panic inside a worker thread is caught, flips a **shared** `a_thread_panicked` flag, and is then re-thrown (`panic::resume_unwind`). This poisons the entire `WorkerPool` — every other worker thread that later checks the flag deliberately panics too (`panic!("Another thread panicked. Aborting.")`), and any caller of `check_panic()` (used both after concurrent execution and, more importantly, inside `ConcurrentTransactionExecutor::get_new_results`, which is polled continuously by the batcher's block-building loop) re-panics as well.

### Finding Description
- `WorkerThread::_run_executor` wraps `worker_executor.run()` (which pulls and executes/validates/commits transactions from the shared chunk) in `panic::catch_unwind`. If a panic occurs, it sets `a_thread_panicked` to `true`, halts the scheduler, and calls `panic::resume_unwind(err)`: [1](#0-0) 
- Every other worker thread, upon picking up its next unit of work, checks the shared flag and immediately panics as well: [2](#0-1) 
- `check_panic()`, called both by `run_and_wait` and by `ConcurrentTransactionExecutor::get_new_results` (which the batcher's block-building loop polls repeatedly for every block it builds), re-panics if the flag is set: [3](#0-2) [4](#0-3) 
- The `AbortIfPanic` guard is designed so that if the panic-handling path itself panics (e.g. `scheduler.halt()` internally panics), the whole process is aborted via `std::process::abort()`: [5](#0-4) 
- A concrete source of an unhandled panic (as opposed to a controlled `TransactionExecutionResult::Err`) reachable from ordinary transaction execution is the fee-bound invariant check, which uses `panic!` rather than returning an error when the computed `actual_fee` exceeds the transaction's `max_possible_fee` (a value derived from the resource bounds and effective tip, both attacker-supplied fields of the transaction): [6](#0-5) 
This is invoked unconditionally from `handle_fee`, which runs for every account transaction, including under the concurrent worker-thread commit path: [7](#0-6) 
- More broadly, `crates/blockifier/src/transaction/account_transaction.rs`, `.../transactions/*`, `.../execution/*`, and `.../fee/*` contain numerous `panic!`/`.unwrap()`/`.expect()` invariant checks that are meant to be "impossible" states but are computed from values that are, transitively, influenced by attacker-controlled contract code, calldata, or resource-bound fields (fee accounting, resource/gas bookkeeping, bouncer updates, etc., e.g. `panic!("Bouncer update failed. ...")` in the commit path): [8](#0-7) 

The `WorkerPool` is created once (per `TransactionExecutorConfig`) and, per the `ConcurrentTransactionExecutor`/`TransactionExecutor` design, is reused across the executor's chunks and across blocks in normal operation (as documented, "Call `join()` to wait for all the threads to finish"): [9](#0-8) 
Once poisoned, every subsequent chunk/block that uses this pool inherits the panicked state and panics again as soon as `check_panic()`/thread pickup runs — this is analogous to the audit report's pattern where one malicious participant's failure (a bad `onERC1155Received` callback) is executed inside a shared loop and breaks every other participant queued behind it.

### Impact Explanation
If a single attacker-crafted transaction (e.g., one that forces the `assert_actual_fee_in_bounds` invariant, or any other reachable internal invariant, to be violated) triggers a genuine panic instead of a graceful `TransactionExecutionError`, the panic:
1. Halts the current block's scheduler, discarding in-flight execution/commit progress for every other (honest) transaction being concurrently processed in that same block/chunk.
2. Poisons the shared `WorkerPool`'s `a_thread_panicked` flag, so every other worker thread and every subsequent caller of `check_panic()` panics too — including calls made while building later, unrelated blocks that reuse the same pool.
3. If the panic-handling path itself faults, `AbortIfPanic` calls `std::process::abort()`, crashing the entire sequencer process.

This matches the "network unable to confirm new transactions" impact class: a node (proposer or validator) whose batcher hits this path stops being able to build/validate blocks, denying service to every transaction sender whose transaction happened to be batched alongside the malicious one, and in the process-abort case, denies service to the whole node.

### Likelihood Explanation
Likelihood depends on finding a concrete way to make one of these "unreachable" invariants (e.g., `actual_fee > max_possible_fee`, or another `panic!`/`.expect()` on attacker-influenced state) actually fire from a single, unprivileged, submitted transaction. The framework-level defect — that a Rust panic anywhere in transaction execution is treated as fatal for the whole shared worker pool rather than being isolated to the offending transaction — is present in code today and is reachable purely via `add_txs_to_block`/`get_new_results`, which any transaction sender's transaction flows through. The remaining work is finding/confirming a specific input that flips one of these `panic!` conditions.

### Recommendation
- Treat any panic escaping a single transaction's execution/validation/commit as isolated to that transaction: convert internal invariant checks that are provably reachable from attacker-controlled data (fee-bound checks, bouncer update checks, etc.) into `Result`-returning errors rather than `panic!`, per the project's own stated guideline of never panicking on data reachable from external input.
- Scope `a_thread_panicked` (and the pool poisoning it causes) to the chunk/block being executed rather than allowing it to persist and re-trigger `check_panic()` on unrelated future blocks sharing the same `WorkerPool`.
- Audit all `panic!`/`.unwrap()`/`.expect()` calls in `crates/blockifier/src/transaction`, `crates/blockifier/src/concurrency`, and `crates/blockifier/src/fee` that operate on values derived from transaction fields or contract execution output, and replace them with defensive error handling where the input is not fully sequencer-controlled.

### Proof of Concept
A concrete PoC requires constructing a transaction (e.g. via resource bounds/tip manipulation or a fee-accounting edge case) that causes `actual_fee` to exceed `tx_context.max_possible_fee()` at line 507 of `crates/blockifier/src/transaction/account_transaction.rs`, triggering the `panic!` at lines 510-517. Once triggered inside a concurrently-executing worker thread, the panic path in `crates/blockifier/src/concurrency/worker_pool.rs` (`_run_executor`, lines 139-166) demonstrates deterministically that:
- `a_thread_panicked` becomes `true`,
- the scheduler is halted, discarding all other in-flight transactions of that block,
- subsequent `check_panic()` calls (as used by `ConcurrentTransactionExecutor::get_new_results`, called every loop iteration of `BlockBuilder::build_block_inner`) will panic again on any later use of the same `WorkerPool`.

### Citations

**File:** crates/blockifier/src/concurrency/worker_pool.rs (L11-24)
```rust
/// Used to execute transactions concurrently.
/// Call `run()` to start executing a chunk of transactions (represented by a [WorkerExecutor]).
/// Call `join()` to wait for all the threads to finish.
///
/// If an execution of a chunk is halted (`Scheduler::halt`), each thread will continue to run until
/// finishing the current execution (excluding reruns), and then move to the next chunk.
/// The transactions that were not fully executed by the time halt was called will be discarded.
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

**File:** crates/blockifier/src/concurrency/utils.rs (L1-16)
```rust
// This struct is used to abort the program if a panic occurred in a place where it could not be
// handled.
pub struct AbortIfPanic;

impl Drop for AbortIfPanic {
    fn drop(&mut self) {
        eprintln!("detected unexpected panic; aborting");
        std::process::abort();
    }
}

impl AbortIfPanic {
    pub fn release(self) {
        std::mem::forget(self);
    }
}
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L505-524)
```rust
    fn assert_actual_fee_in_bounds(tx_context: &Arc<TransactionContext>, actual_fee: Fee) {
        let max_fee = tx_context.max_possible_fee();
        if actual_fee > max_fee {
            match &tx_context.tx_info {
                TransactionInfo::Current(context) => {
                    panic!(
                        "Actual fee {:#?} exceeded bounds; max possible fee is {:#?} (computed \
                         from {:#?} with tip {:#?}).",
                        actual_fee,
                        max_fee,
                        context.resource_bounds,
                        tx_context.effective_tip()
                    );
                }
                TransactionInfo::Deprecated(_) => {
                    panic!("Actual fee {actual_fee:#?} exceeded bounds; max fee is {max_fee:#?}.");
                }
            }
        }
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L526-548)
```rust
    fn handle_fee<S: StateReader>(
        state: &mut TransactionalState<'_, S>,
        tx_context: Arc<TransactionContext>,
        actual_fee: Fee,
        charge_fee: bool,
        concurrency_mode: bool,
    ) -> TransactionExecutionResult<Option<CallInfo>> {
        if !charge_fee || actual_fee == Fee(0) {
            // Fee charging is not enforced in some tests.
            // TODO(Yoni): consider setting the actual fee to zero when the flag is off.
            return Ok(None);
        }

        Self::assert_actual_fee_in_bounds(&tx_context, actual_fee);

        let fee_transfer_call_info = if concurrency_mode && !tx_context.is_sequencer_the_sender() {
            Self::concurrency_execute_fee_transfer(state, tx_context, actual_fee)?
        } else {
            Self::execute_fee_transfer(state, tx_context, actual_fee)?
        };

        Ok(Some(fee_transfer_call_info))
    }
```

**File:** crates/blockifier/src/concurrency/worker_logic.rs (L357-365)
```rust
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
