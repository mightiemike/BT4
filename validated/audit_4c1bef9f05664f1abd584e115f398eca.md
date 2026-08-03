[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/block-executor/src/scheduler_status.rs (L112-120)
```rust
============================== Notes on Method Call Concurrency ==============================

In general, most methods in this module can be called concurrently with the following exceptions:

1. Each successful [ExecutionStatuses::add_stall] call must be balanced by a
   corresponding [ExecutionStatuses::remove_stall] call that starts after the add_stall
   call completes. Multiple concurrent add_stall and remove_stall calls on the same
   transaction status are supported as long as this balancing property is maintained.

```

**File:** aptos-move/block-executor/src/scheduler_status.rs (L417-461)
```rust
    pub(crate) fn remove_stall(&self, txn_idx: TxnIndex) -> Result<bool, PanicError> {
        let status = &self.statuses[txn_idx as usize];
        let prev_num_stalls = status.num_stalls.fetch_sub(1, Ordering::SeqCst);

        if prev_num_stalls == 0 {
            return Err(code_invariant_error(
                "remove_stall called when num_stalls == 0",
            ));
        }

        if prev_num_stalls == 1 {
            // Acquire write lock for (non-monitor) shortcut modifications.
            let status_guard = status.status_with_incarnation.lock();

            // num_stalls updates are not under the lock, so need to re-check (otherwise
            // a different add_stall might have already incremented the count).
            if status.is_stalled() {
                return Ok(false);
            }

            if let Some(incarnation) = status_guard.pending_scheduling() {
                if incarnation == 0 {
                    // Invariant due to scheduler logic: for a successful remove_stall there
                    // must have been an add_stall for incarnation 0, which is impossible.
                    return Err(code_invariant_error("0-th incarnation in remove_stall"));
                }
                self.execution_queue_manager
                    .add_to_schedule(incarnation == 1, txn_idx);
            } else if status_guard.is_executed() {
                // TODO(BlockSMTv2): Here, when waiting is supported, if inner status is executed,
                // would need to notify waiting workers.

                // Status is Executed so the dependency status may not be WaitForExecution
                // (finish_execution sets ShouldDefer or IsSafe dependency status).
                status.swap_dependency_status_any(
                    &[DependencyStatus::ShouldDefer, DependencyStatus::IsSafe],
                    DependencyStatus::IsSafe,
                    "remove_stall",
                )?;
            }

            return Ok(true);
        }
        Ok(false)
    }
```
