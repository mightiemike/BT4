[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** aptos-move/block-executor/src/cold_validation.rs (L80-91)
```rust
    // txn_idx -> (incarnation, is_executing) implies that the specified incarnation
    // of the txn requires additional uncommon / cold validation to be performed before
    // it can be committed. At the time when the active requirement was recorded,
    // the status of the given incarnation must have been Executing or Executed (as
    // otherwise new incarnation will read updated information and not require additional
    // validation). The boolean is_executing distinguishes between the two cases.
    //
    // The bool flag is mainly stored for convenience and slight performance optimization
    // for when is_executing is false (as processing the requirement in this case does
    // not need to acquire the status lock). However, when is_executing is true, processing
    // the requirement does need to double check whether the txn is still executing.
    versions: BTreeMap<TxnIndex, (Incarnation, bool)>,
```

**File:** aptos-move/block-executor/src/cold_validation.rs (L174-181)
```rust
    /// finishes. Below array tracks the status of deferred requirements:
    /// The bits except 2 least significant contain an affected incarnation, while the
    /// last two bits encode the following:
    /// 00: default: incarnation is not affected.
    /// 01: requirement is deferred until the txn finishes execution.
    /// 10: requirement is completed.
    /// 11: unreachable.
    deferred_requirements_status: Vec<CachePadded<AtomicU32>>,
```

**File:** aptos-move/block-executor/src/cold_validation.rs (L437-450)
```rust
    /// Correctness of this method relies on the assumption that calls are for monotonically
    /// increasing txn_idx, which holds for BlockSTMv2 as the method is used to check if the
    /// next idx can be committed.
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

**File:** aptos-move/block-executor/src/cold_validation.rs (L453-459)
```rust
fn blocked_incarnation_status(incarnation: Incarnation) -> u32 {
    (incarnation << 2) | 1
}

fn unblocked_incarnation_status(incarnation: Incarnation) -> u32 {
    (incarnation << 2) | 2
}
```

**File:** aptos-move/block-executor/src/cold_validation.rs (L896-904)
```rust
                assert!(requirements.is_commit_blocked(txn_idx, incarnation));

                assert_ok_eq!(
                    requirements.validation_requirement_processed(1, txn_idx, incarnation, false),
                    txn_idx == 6
                );

                assert!(!requirements.is_commit_blocked(txn_idx, incarnation));
            }
```
