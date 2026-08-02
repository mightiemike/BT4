# No vulnerability found for this question.

**Analysis:**

The reported concern describes a scenario where `is_commit_blocked` reads `min_idx_with_unprocessed_validation_requirement` with `Ordering::Acquire` while `deferred_requirements_status[txn_idx]` is read with `Ordering::Relaxed`, allegedly allowing a torn/stale view that lets a genuinely-blocked transaction commit. Tracing the actual code shows this is not possible:

1. **Write-side ordering** (`validation_requirement_processed`) writes `deferred_requirements_status[txn_idx]` with `Relaxed` *before*, in program order, calling `advance_min_unprocessed_idx`, which performs the `Release` swap on `min_idx_with_unprocessed_validation_requirement`. [1](#0-0) 

2. **Read-side ordering** (`is_commit_blocked`) loads `min_idx_with_unprocessed_validation_requirement` with `Acquire` first; only if that check is `false` (short-circuit `||`) does it then load `deferred_requirements_status[txn_idx]` with `Relaxed`. [2](#0-1) 

This is the standard "guard relaxed accesses with a release/acquire pair" idiom. Because the writer's `Relaxed` store to `deferred_requirements_status` is sequenced-before its `Release` store to `min_idx`, and the reader's `Acquire` load of `min_idx` is sequenced-before its `Relaxed` load of `deferred_requirements_status` (enforced by Rust's short-circuit `||` evaluation and per-thread program-order guarantees for atomics), any reader that observes the advanced `min_idx` value is guaranteed — via the `happens-before` chain (sequenced-before → synchronizes-with → sequenced-before) — to also observe the corresponding `deferred_requirements_status` update. There is no interleaving in which the reader sees `min_idx` advanced past `txn_idx` without also seeing the correct (blocked) deferred status.

The code's authors were explicitly aware of this synchronization requirement and documented it directly in the source: [3](#0-2) [4](#0-3) 

The extensive module-level comment also documents the at-most-one-pending-requirement invariant and the strict caller contract (commit-hook-only) that this design relies on. [5](#0-4) 

No unprivileged transaction-sequence construction can force `is_commit_blocked` to return `false` while a genuine cold-validation requirement is outstanding — the mechanism is a deliberate and correctly-implemented release/acquire pairing, not a race condition. This does not corrupt committed state, proof material, or authenticated responses, so it fails the State-Integrity Gate.

### Citations

**File:** aptos-move/block-executor/src/cold_validation.rs (L62-72)
```rust
 * ***IMPORTANT***: The current implementation stores at most one pending requirement
 * (Option instead of Vec). This simplification relies on the fact that requirements
 * are recorded exclusively during the sequential commit hook (record_requirements
 * is called from prepare_and_queue_commit_ready_txn). After recording, the
 * min_idx_with_unprocessed_validation_requirement is lowered to block the next
 * commit via is_commit_blocked, preventing a second record_requirements call
 * before the first pending requirement is activated and processed. If requirements
 * were ever to be added outside the commit path (e.g. from execution or another
 * concurrent source), this assumption would break, and pending_requirements would
 * need to be changed back to a Vec (see commit 0e134ddb6b on main for the prior
 * Vec-based implementation).
```

**File:** aptos-move/block-executor/src/cold_validation.rs (L407-421)
```rust
        if validation_still_needed {
            // min_idx_with_unprocessed_validation_requirement may be increased below, after
            // deferred status is already updated. When checking if txn can be committed, the
            // access order is opposite, ensuring that if minimum index is higher, we will
            // also observe the deferred status set below. This relies on the Release/Acquire
            // pair on min_idx_with_unprocessed_validation_requirement.
            //
            // The reason for using fetch_max is because the deferred requirement can be
            // fulfilled by a different worker (the one executing the txn), which may report
            // the requirement as completed before the current worker sets the status here.
            self.deferred_requirements_status[txn_idx as usize]
                .fetch_max(blocked_incarnation_status(incarnation), Ordering::Relaxed);
        }

        self.advance_min_unprocessed_idx(active_reqs);
```

**File:** aptos-move/block-executor/src/cold_validation.rs (L440-450)
```rust
    pub(crate) fn is_commit_blocked(&self, txn_idx: TxnIndex, incarnation: Incarnation) -> bool {
        // The order of checks is important to avoid a concurrency bugs (since recording
        // happens in the opposite order). We first check that there are no unscheduled
        // requirements below (incl.) the given index, and then that there are no scheduled
        // but yet unfulfilled (validated) requirements for the index.
        self.min_idx_with_unprocessed_validation_requirement
            .load(Ordering::Acquire)
            <= txn_idx
            || self.deferred_requirements_status[txn_idx as usize].load(Ordering::Relaxed)
                == blocked_incarnation_status(incarnation)
    }
```
