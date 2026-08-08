### Title
Unrecoverable infinite spin-loop in `Bank::freeze()` if a spawned accounts-lt-hash worker panics - ([File: runtime/src/bank/accounts_lt_hash.rs])

### Summary
The reported RNG bug class is: an async/background operation is awaited by a state-machine with no fallback/exit path, so if that background operation fails (or never completes), the caller is stuck forever with no way to recover. The closest reachable analog in agave's `AccountsDB`/lattice-hashing machinery is `AccountsLtHashAsyncProgress::finish()`, which busy-spins waiting for a fixed-size Rayon thread pool to decrement a pending-job counter, with no timeout, no error channel, and no fallback path if a spawned job panics before decrementing the counter.

### Finding Description
Every account update processed on-chain (`enqueue_on_chain_accounts_lt_hash_updates`) or off-chain is enqueued as an async job into a global Rayon thread pool (`accounts_hasher_thread_pool()`), tracked via `AccountsLtHashAsyncProgress::num_jobs_pending` [1](#0-0) . `Bank::freeze()` — which is called on every bank to finalize its state and is mandatory before the bank can be rooted/replayed further — calls `finish_accounts_lt_hash_updates()`, which in turn calls `AccountsLtHashAsyncProgress::finish()`: [2](#0-1) 

```rust
fn finish(&self, lt_hash: &mut LtHash) -> u64 {
    while self.num_jobs_pending.load(Ordering::Relaxed) > 0 {
        // Spin, do not yield! This is called by Bank::freeze() and we want to be fast.
        hint::spin_loop();
    }
    ...
}
```

This is analogous to the reported pattern: `_requireNotLocked()`/pending RNG state that can only be exited by the async operation completing successfully, with no `exitAwardPhase()`-style escape hatch. Here, the "exit" condition (`num_jobs_pending == 0`) is only ever satisfied by the spawned closure itself decrementing the counter *after* successfully running `Self::process(...)`: [3](#0-2) 

```rust
fn spawn(&self, thread_pool: &'static ThreadPool, update: AccountsLtHashUpdate) {
    self.num_jobs_pending.fetch_add(1, Ordering::Relaxed);
    ...
    thread_pool.spawn({
        ...
        move || {
            ...
            Self::process(&mut accumulator.lock().unwrap(), update);
            // Decrementing the number of pending jobs MUST happen *after*
            // accumulating the result.
            num_jobs_pending.fetch_sub(1, Ordering::Relaxed);
        }
    });
}
```

If `Self::process()` (which calls `AccountsDb::lt_hash_account()` on both the previous and current account states) panics for any reason — e.g., an unexpected/corrupted account data shape reachable from user-controlled account content, an allocation failure, or any future code change that introduces an unwrap/index-out-of-bounds on account bytes — the spawned closure unwinds and the `fetch_sub` line is never reached. Because `ThreadPool::spawn()` jobs run detached (no `Result` is observed by the caller) and Rayon's default is to abort a *panicking thread's current task* but continue running the pool, the panic is silently swallowed (or, depending on Rayon panic-handling configuration, could poison the pool), and `num_jobs_pending` remains permanently elevated. There is no timeout, no error propagation, and no recovery mechanism analogous to `exitAwardPhase()` — `finish()`'s `while ... spin_loop()` loop will never terminate.

### Impact Explanation
`Bank::freeze()` is on the critical path for every bank in the validator (both leader block production and replay of other validators' blocks) [4](#0-3) . A permanent hang inside `finish_accounts_lt_hash_updates()` blocks `freeze()` from ever returning, which halts the bank-processing pipeline entirely — the validator can no longer freeze/root new banks, causing a full validator hang/DoS (node panic-equivalent, unrecoverable without a process restart), exactly mirroring the "permanently locked, no exit path" impact described in the report, except here it locks the entire validator's progress rather than a single strategy contract's user funds.

### Likelihood Explanation
This requires a panic to occur inside the spawned lt-hash worker closure (in `Self::process`/`AccountsDb::lt_hash_account`) before the counter decrement. Currently no code path was identified in the reviewed snippets that guarantees such a panic under arbitrary but valid on-chain data — this is a structural/defense-in-depth gap rather than a confirmed exploitable panic today. I could not find or verify a concrete panicking input inside `AccountsDb::lt_hash_account` within the available index (its body was not returned by search), so likelihood is currently unconfirmed and depends on unaudited code deeper in `accounts_db.rs`.

### Recommendation
Add a bounded-wait/error-recovery mechanism to `AccountsLtHashAsyncProgress::finish()` analogous to the report's suggested `exitAwardPhase()`: e.g., wrap the spawned closure body in `std::panic::catch_unwind` and always decrement `num_jobs_pending` in a way that cannot be skipped by a panic (using a guard/`Drop` type), and/or add a timeout with a fatal, loud error/abort instead of an infinite silent spin, so a panicking worker cannot leave the validator irrecoverably wedged.

### Proof of Concept
Not independently reproducible from the available index — the panic-triggering input inside `AccountsDb::lt_hash_account` (called from `Self::process`) could not be located/confirmed in the accessible code, so no concrete on-chain trigger is presented. The control-flow proof above (spin loop with decrement only on the non-panicking path) is directly supported by [5](#0-4) .

### Citations

**File:** runtime/src/bank/accounts_lt_hash.rs (L260-302)
```rust
    /// Enqueues `update` into `thread_pool` for asynchronous processing.
    fn spawn(&self, thread_pool: &'static ThreadPool, update: AccountsLtHashUpdate) {
        self.num_jobs_pending.fetch_add(1, Ordering::Relaxed);
        self.num_jobs_total.fetch_add(1, Ordering::Relaxed);
        thread_pool.spawn({
            let accumulators = Arc::clone(&self.accumulators);
            let num_jobs_pending = Arc::clone(&self.num_jobs_pending);
            move || {
                // SAFETY: We always call from the same/correct Rayon thread pool.
                let worker_index = thread_pool.current_thread_index().unwrap();

                // SAFETY: There are num_threads accumulators, and each
                // thread's index shall always be in range 0..num_threads.
                debug_assert!(worker_index < accumulators.len());
                let accumulator = unsafe { accumulators.get_unchecked(worker_index) };

                Self::process(&mut accumulator.lock().unwrap(), update);

                // Decrementing the number of pending jobs MUST happen *after*
                // accumulating the result.  This ensures `finish()` cannot
                // observe zero pending jobs until all workers are done.
                num_jobs_pending.fetch_sub(1, Ordering::Relaxed);
            }
        });
    }

    /// Waits for all pending jobs to complete, then mixes the results into `lt_hash`.
    ///
    /// Returns the number of asynchronous jobs completed.
    ///
    /// Note: Since an LtHash is large, `lt_hash` is passed as an in-out parameter.
    /// This it to avoid Rust compiler bug that fails to perform return value optimization.
    fn finish(&self, lt_hash: &mut LtHash) -> u64 {
        while self.num_jobs_pending.load(Ordering::Relaxed) > 0 {
            // Spin, do not yield! This is called by Bank::freeze() and we want to be fast.
            hint::spin_loop();
        }

        for thread_accumulator in self.accumulators.iter() {
            lt_hash.mix_in(&thread_accumulator.lock().unwrap());
        }
        self.num_jobs_total.load(Ordering::Relaxed)
    }
```

**File:** runtime/src/bank.rs (L3057-3084)
```rust
    pub fn freeze(&self) {
        // This lock prevents any new commits from BankingStage
        // `Consumer::execute_and_commit_transactions_locked()` from
        // coming in after the last tick is observed. This is because in
        // BankingStage, any transaction successfully recorded in
        // `record_transactions()` is recorded after this `hash` lock
        // is grabbed. At the time of the successful record,
        // this means the PoH has not yet reached the last tick,
        // so this means freeze() hasn't been called yet. And because
        // BankingStage doesn't release this hash lock until both
        // record and commit are finished, those transactions will be
        // committed before this write lock can be obtained here.
        let mut hash = self.hash.write().unwrap();
        if *hash == Hash::default() {
            // finish up any deferred changes to account state
            self.distribute_transaction_fee_details();
            self.update_slot_history();
            self.run_incinerator();

            // freeze is a one-way trip, idempotent
            self.freeze_started.store(true, Relaxed);
            // updating the accounts lt hash must happen *outside* of hash_internal_state() so
            // that rehash() can be called and *not* modify self.accounts_lt_hash.
            self.finish_accounts_lt_hash_updates();
            *hash = self.hash_internal_state();
            self.rc.accounts.accounts_db.mark_slot_frozen(self.slot());
        }
    }
```
