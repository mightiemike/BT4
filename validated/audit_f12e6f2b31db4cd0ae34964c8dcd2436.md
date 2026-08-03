[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** storage/storage-interface/src/state_store/state.rs (L163-165)
```rust
    pub fn is_descendant_of(&self, rhs: &State) -> bool {
        self.shards[0].is_descendant_of(&rhs.shards[0])
    }
```

**File:** storage/storage-interface/src/state_store/state.rs (L456-464)
```rust
impl LedgerState {
    pub fn new(latest: State, last_checkpoint: State) -> Self {
        assert!(latest.is_descendant_of(&last_checkpoint));

        Self {
            latest,
            last_checkpoint,
        }
    }
```

**File:** storage/storage-interface/src/state_store/state.rs (L485-540)
```rust
    pub fn update_with_memorized_reads(
        &self,
        persisted_hot_view: Arc<dyn HotStateView>,
        persisted_snapshot: &State,
        updates: &StateUpdateRefs,
        reads: &ShardedStateCache,
    ) -> Result<(LedgerState, HotStateUpdates)> {
        let _timer = TIMER.timer_with(&["ledger_state__update"]);

        let mut all_hot_state_updates = HotStateUpdates::new_empty();
        let last_checkpoint = if let Some(batched) = updates.for_last_checkpoint_batched() {
            let per_version = updates
                .for_last_checkpoint_per_version()
                .expect("Both per-version and batched updates should exist.");
            let (new_ckpt, hot_state_updates) = self.latest().update(
                Arc::clone(&persisted_hot_view),
                persisted_snapshot,
                batched,
                per_version,
                updates.all_checkpoint_versions(),
                reads,
            )?;
            all_hot_state_updates.for_last_checkpoint = Some(hot_state_updates);
            new_ckpt
        } else {
            self.last_checkpoint.clone()
        };

        let base_of_latest = if updates.for_last_checkpoint_batched().is_none() {
            self.latest()
        } else {
            &last_checkpoint
        };
        let latest = if let Some(batched) = updates.for_latest_batched() {
            let per_version = updates
                .for_latest_per_version()
                .expect("Both per-version and batched updates should exist.");
            let (new_latest, hot_state_updates) = base_of_latest.update(
                persisted_hot_view,
                persisted_snapshot,
                batched,
                per_version,
                &[],
                reads,
            )?;
            all_hot_state_updates.for_latest = Some(hot_state_updates);
            new_latest
        } else {
            base_of_latest.clone()
        };

        Ok((
            LedgerState::new(latest, last_checkpoint),
            all_hot_state_updates,
        ))
    }
```

**File:** storage/storage-interface/src/state_store/sharded_jmt_state.rs (L76-78)
```rust
    pub fn is_descendant_of(&self, rhs: &Self) -> bool {
        self.shards[0].is_descendant_of(&rhs.shards[0])
    }
```
