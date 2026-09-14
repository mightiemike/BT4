### Title
Table/instance semaphore permit leak on contended-acquire failure path — permanent WASM-execution-slot starvation - (File: `runtime/near-vm-runner/src/wasmtime_runner/mod.rs`)

### Summary
`ConcurrencySemaphore::try_acquire` (`runtime/near-vm-runner/src/wasmtime_runner/mod.rs:235-277`) implements a lock-free, manually-managed reservation counter for wasmtime execution "table" and "instance" slots. In the contended retry loops, a slot is speculatively reserved via `fetch_add` (`try_reserve_tables`/`try_reserve_instance`) *before* the code decides whether to release it again. On an extremely-long contention streak the loop bails out early via `iterations.checked_add(1)?` — the `?` operator returns `None` from `try_acquire` immediately, but this happens *after* the failed `fetch_add` already incremented the shared `tables`/`instances` `AtomicU64` counters and *before* the corresponding `release_tables`/`release_instance` call that would undo it. The reserved slot is never released, exactly analogous to the Xen `XENMEM_acquire_resource` bug where an RCU reference is taken but the error path returns without releasing it.

### Finding Description
`try_acquire` (`mod.rs:235`) reserves table slots like this:
```
while !self.try_reserve_tables(num_tables) {
    iterations = iterations.checked_add(1)?;
    if self.release_tables(num_tables) <= self.max_tables.into() {
        continue;
    }
    self.release_notify.wait(&mut guard);
}
```
`try_reserve_tables` (`mod.rs:219`) unconditionally does `tables.fetch_add(n, Acquire)` as the loop condition, so every failed iteration of the `while` already incremented `tables` by `num_tables`. The *only* code path that undoes this increment is the subsequent `self.release_tables(num_tables)` call inside the loop body. But the line immediately preceding it, `iterations = iterations.checked_add(1)?;`, can itself return `None` (once `iterations` reaches `u16::MAX`), which propagates via `?` straight out of `try_acquire` — skipping the `release_tables` call entirely. The same pattern exists for the instance-slot loop at `mod.rs:262-267`.
The result: `tables`/`instances` is left permanently inflated by `num_tables`/`1` for every occurrence of this race, with no owning `InstancePermit` ever created to release it later (contrast with the correctly-paired `Drop for InstancePermit` at `mod.rs:164-173`, which is the RAII mechanism used on the success path).

`try_acquire` is invoked once per contract-function-call VM instantiation, at `with_compiled_and_loaded`/`run` (`mod.rs:1025`, in `runtime/near-vm-runner/src/wasmtime_runner/mod.rs:940-1032`), which is on the direct path of `action_function_call` (`runtime/runtime/src/function_call.rs`), itself reachable by any account submitting a `FunctionCall` action/receipt (`runtime/runtime/src/lib.rs` — `apply_action` dispatch table, documented in `protocol-model/spec/runtime-execution.md:77-90`). Any unprivileged transaction signer or receipt sender that triggers a `FunctionCall` therefore exercises this code.

### Impact Explanation
`tables`/`instances` are process-global counters (`MAX_CONCURRENCY = 1_000` instances, `max_tables` configured per node) shared by every concurrently executing contract call on the node. Each leak permanently reduces the effective concurrency ceiling; the ceiling never recovers because there is no compensating decrement anywhere else in the code. Given enough leaks, `try_reserve_instance`/`try_reserve_tables` will always report "over limit", `try_acquire` will always fall into (and eventually exhaust) the contention loop, and ultimately `with_compiled_and_loaded`'s `concurrency.try_acquire` call will return `None`, causing every `FunctionCall` on that node to abort with `FunctionCallError::LinkError { msg: "failed to acquire execution slot" }` (`mod.rs:1025-1031`). This is a transaction-triggered degradation of contract-call capacity on the affected validator/RPC node — the direct analog of the Xen livelock DoS, where a leaked resource permanently starves a shared counter needed by unrelated future operations.

### Likelihood Explanation
Triggering the leak requires the retry loop to execute `u16::MAX` (65,536) consecutive failed reservation attempts under the `release_mutex` without ever succeeding or falling into the `Condvar::wait` branch (i.e., `release_tables`/`release_instance` must repeatedly observe the counter still above the limit on every one of 65,536 spins, which is what keeps re-entering `continue` rather than `wait`). This requires sustained extreme contention on `MAX_CONCURRENCY`/`max_tables_per_contract`-sized pools, which is plausible only under deliberately induced heavy concurrent WASM-call load, and each successful trigger only leaks a small, bounded amount (`num_tables` or `1`) per occurrence. It is a real, reachable code path with no privilege requirement, but requires many repeated leak events to accumulate to full exhaustion, making it a lower-likelihood, cumulative DoS rather than a single-transaction kill switch. It is comparable to the original Xen CVE's likelihood, which was also rated Medium.

### Recommendation
Move the `iterations` bound check before the speculative `fetch_add`, or restructure the loop so that any early return from the contended path first calls `release_tables`/`release_instance` to undo the reservation — i.e., release-then-bail rather than bail-before-release. Alternately, wrap the reservation in an RAII guard from the moment `fetch_add` succeeds/fails so `?`-based early returns can't skip the compensating decrement, consistent with how `InstancePermit`'s `Drop` already guarantees release on the success path.

### Proof of Concept
Conceptual (cannot be fully driven without a live cluster/benchmark harness):
1. Configure a node with a small `max_tables_per_contract`/effective `MAX_CONCURRENCY` pool (or drive normal-size pools with many parallel receipts).
2. From many unprivileged accounts, submit a sustained flood of `FunctionCall` receipts to different contracts that each require a table (i.e., contracts using WASM tables), keeping the pool saturated so that `try_reserve_tables`/`try_reserve_instance` keeps failing on retries in `try_acquire`.
3. Sustain contention long enough that a single call's retry loop spins 65,536 times without success (achieved by keeping the mutex/pool oscillating near the limit) so that `iterations.checked_add(1)?` returns `None`, leaking the just-reserved slot count permanently into `tables`/`instances`.
4. Repeat step 2–3 enough times; eventually `tables`/`instances` never drops below the configured max, and all subsequent `FunctionCall` receipts on the node fail with `"failed to acquire execution slot"`, degrading/halting contract execution capacity network-wide as more validators are driven into the same state.

Note: I was unable to execute or benchmark this scenario (no runtime/terminal access), so the practical achievability of 65,536 consecutive failed spins under real scheduler timing is not empirically confirmed — this assessment is based on static code-path analysis of `runtime/near-vm-runner/src/wasmtime_runner/mod.rs:235-277`.