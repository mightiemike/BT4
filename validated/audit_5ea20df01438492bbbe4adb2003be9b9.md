## Finding

### Title
`ExecutionProxy::sync_to_target` unconditionally advances `latest_logical_time` even when the underlying state-sync call fails, causing the node to believe it is synced to a target it never actually reached - ([File: consensus/src/state_computer.rs])

### Summary
`ExecutionProxy::sync_to_target` in `consensus/src/state_computer.rs` updates the shared `latest_logical_time` guard to the requested target **before checking whether the state-sync notification actually succeeded**. This is the same "update tracked state regardless of downstream revert/failure" pattern as the external `OracleFeeDistributor._sendValue` report, translated to Aptos's consensus/state-sync handoff.

### Finding Description
`ExecutionProxy` holds a `write_mutex: AsyncMutex<LogicalTime>` that gates re-entrant sync calls between consensus and state sync: [1](#0-0) 

```
async fn sync_to_target(&self, target: LedgerInfoWithSignatures) -> Result<(), StateSyncError> {
    let mut latest_logical_time = self.write_mutex.lock().await;
    let target_logical_time = ...;
    self.executor.finish();
    if *latest_logical_time >= target_logical_time {
        // short-circuits: assumes we're already synced past target
        return Ok(());
    }
    ...
    let result = monitor!("sync_to_target", self.state_sync_notifier.sync_to_target(target).await);

    // Update the latest logical time
    *latest_logical_time = target_logical_time;   // <-- unconditional, even if result is Err
    ...
    result.map_err(...)
}
``` [2](#0-1) 

Compare this to the sibling method `sync_for_duration`, which correctly guards the update behind `if let Ok(...) = &result`: [3](#0-2) 

In `sync_to_target`, the assignment `*latest_logical_time = target_logical_time;` happens unconditionally after `self.state_sync_notifier.sync_to_target(target).await` returns, whether that call resolved `Ok` or `Err`. The `sync_to_target` call can genuinely fail — e.g., a timeout inside `ConsensusNotifier::sync_to_target` (`state-sync/inter-component/consensus-notifications/src/lib.rs:181-207`) or an explicit `Error::OldSyncRequest`/other error path from the state-sync driver (`state-sync/state-sync-driver/src/notification_handlers.rs:261-318`), or the injected `fail_point!("consensus::sync_to_target", ...)`.

When that happens, storage was never actually advanced to `target`, yet the in-memory `latest_logical_time` now claims it was. On the very next call to `sync_to_target` (or `sync_for_duration`, since both share the same `write_mutex`) with an equal-or-lower target, the early-exit guard at line 198 (`*latest_logical_time >= target_logical_time`) fires and the function returns `Ok(())` immediately — without ever re-invoking the state-sync notifier. The consensus/execution layer therefore believes it is caught up to a commit that its local storage never actually persisted.

### Impact Explanation
This breaks the version/round binding invariant between consensus's view of "what has been committed locally" and the actual state of durable storage. Once corrupted, the node:
- Skips subsequent legitimate sync-to-target requests for the same or lower round, since the guard treats it as already-synced.
- Can proceed with block execution/voting assuming its storage state matches a ledger version that was never actually written, creating a genuine root-hash/version mismatch between the node's real state and what it advertises as committed.

This is a state/version integrity break in the executor-to-storage handoff described by the "Proof and Storage Pivots" section (authenticated state must stay bound to the correct version). In the best case, this divergence is eventually caught by consensus's own `commit_info` mismatch check/panic in `consensus/src/pipeline/buffer_item.rs:25-78`, turning the corruption into a node crash; in other flows it can leave the node silently serving/reasoning with an unsynced state view.

### Likelihood Explanation
This does not require any attacker privilege — any transient failure in the state-sync layer (timeout, dropped channel, injected fail point, an already-in-progress conflicting sync request returning `Error::InvalidSyncRequest`/`OldSyncRequest`) triggers the unconditional write. Because `sync_to_target` is invoked on ordinary consensus/consensus-observer commit-decision paths (`consensus/src/pipeline/execution_client.rs:695-706`, `consensus/src/consensus_observer/observer/state_sync_manager.rs:189-231`), the code path is exercised regularly in production, not just adversarially.

### Recommendation
Mirror the fix pattern already used in `sync_for_duration`: only update `*latest_logical_time` when `result` is `Ok`:
```rust
let result = monitor!("sync_to_target", self.state_sync_notifier.sync_to_target(target).await);
if result.is_ok() {
    *latest_logical_time = target_logical_time;
}
```
This prevents the guard from being advanced past a sync that never actually completed.

### Proof of Concept
1. Call `ExecutionProxy::sync_to_target(target)` where `self.state_sync_notifier.sync_to_target(target)` returns `Err` (reproducible via the existing `fail_point!("consensus::sync_to_target", ...)` in the same function, or naturally via a state-sync timeout/`OldSyncRequest`/`InvalidSyncRequest` from `state-sync/state-sync-driver/src/notification_handlers.rs:261-318`).
2. Observe that despite the `Err`, `*latest_logical_time` is set to `target_logical_time` at line 232 before the error is returned.
3. Immediately call `sync_to_target` again with the same (or a lower) target: the guard at line 198 (`*latest_logical_time >= target_logical_time`) now returns `Ok(())` without contacting state sync at all, even though storage was never advanced to that version. [4](#0-3)

### Citations

**File:** consensus/src/state_computer.rs (L163-173)
```rust
        let result = monitor!(
            "sync_for_duration",
            self.state_sync_notifier.sync_for_duration(duration).await
        );

        // Update the latest logical time
        if let Ok(latest_synced_ledger_info) = &result {
            let ledger_info = latest_synced_ledger_info.ledger_info();
            let synced_logical_time = LogicalTime::new(ledger_info.epoch(), ledger_info.round());
            *latest_logical_time = synced_logical_time;
        }
```

**File:** consensus/src/state_computer.rs (L186-243)
```rust
    /// Synchronize to a commit that is not present locally.
    async fn sync_to_target(&self, target: LedgerInfoWithSignatures) -> Result<(), StateSyncError> {
        // Grab the logical time lock and calculate the target logical time
        let mut latest_logical_time = self.write_mutex.lock().await;
        let target_logical_time =
            LogicalTime::new(target.ledger_info().epoch(), target.ledger_info().round());

        // Before state synchronization, we have to call finish() to free the
        // in-memory SMT held by BlockExecutor to prevent a memory leak.
        self.executor.finish();

        // The pipeline phase already committed beyond the target block timestamp, just return.
        if *latest_logical_time >= target_logical_time {
            warn!(
                "State sync target {:?} is lower than already committed logical time {:?}",
                target_logical_time, *latest_logical_time
            );
            return Ok(());
        }

        // This is to update QuorumStore with the latest known commit in the system,
        // so it can set batches expiration accordingly.
        // Might be none if called in the recovery path, or between epoch stop and start.
        if let Some(inner) = self.state.read().as_ref() {
            let block_timestamp = target.commit_info().timestamp_usecs();
            inner
                .payload_manager
                .notify_commit(block_timestamp, Vec::new());
        }

        // Inject an error for fail point testing
        fail_point!("consensus::sync_to_target", |_| {
            Err(anyhow::anyhow!("Injected error in sync_to_target").into())
        });

        // Invoke state sync to synchronize to the specified target. Here, the
        // ChunkExecutor will process chunks and commit to storage. However, after
        // block execution and commits, the internal state of the ChunkExecutor may
        // not be up to date. So, it is required to reset the cache of the
        // ChunkExecutor in state sync when requested to sync.
        let result = monitor!(
            "sync_to_target",
            self.state_sync_notifier.sync_to_target(target).await
        );

        // Update the latest logical time
        *latest_logical_time = target_logical_time;

        // Similarly, after state synchronization, we have to reset the cache of
        // the BlockExecutor to guarantee the latest committed state is up to date.
        self.executor.reset()?;

        // Return the result
        result.map_err(|error| {
            let anyhow_error: anyhow::Error = error.into();
            anyhow_error.into()
        })
    }
```
