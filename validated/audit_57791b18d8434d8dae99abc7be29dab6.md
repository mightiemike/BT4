[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** execution/executor-types/src/state_checkpoint_output.rs (L25-39)
```rust
    pub fn new(
        state_summary: LedgerStateSummary,
        state_checkpoint_hashes: Vec<Option<HashValue>>,
        hot_state_checkpoint_hashes: Option<Vec<Option<HashValue>>>,
        position_state_summary: Option<LedgerWithSummary<PositionStateWithSummary>>,
        position_state_checkpoint_hashes: Option<Vec<Option<HashValue>>>,
    ) -> Self {
        Self::new_impl(Inner {
            state_summary,
            state_checkpoint_hashes,
            hot_state_checkpoint_hashes,
            position_state_summary,
            position_state_checkpoint_hashes,
        })
    }
```

**File:** execution/executor-types/src/state_checkpoint_output.rs (L67-75)
```rust
    pub fn reconfig_suffix(&self) -> Self {
        // An empty reconfig-suffix block produces no position writes, so the
        // position state is unchanged — propagate it for the next block's
        // freeze base.
        Self::new_empty(
            self.state_summary.clone(),
            self.position_state_summary.clone(),
        )
    }
```

**File:** execution/executor-types/src/state_checkpoint_output.rs (L85-88)
```rust
    /// Native-position summary after this chunk (latest + last_checkpoint),
    /// computed at execution time, persisted at commit without recompute.
    /// `None` unless the position-state-root feature is on.
    pub position_state_summary: Option<LedgerWithSummary<PositionStateWithSummary>>,
```

**File:** storage/storage-interface/src/chunk_to_commit.rs (L17-63)
```rust
#[derive(Clone)]
pub struct ChunkToCommit<'a> {
    pub first_version: Version,
    pub transactions: &'a [Transaction],
    pub persisted_auxiliary_infos: &'a [PersistedAuxiliaryInfo],
    pub transaction_outputs: &'a [TransactionOutput],
    pub transaction_infos: &'a [TransactionInfo],
    pub state: &'a LedgerState,
    pub state_summary: &'a LedgerStateSummary,
    pub state_update_refs: &'a StateUpdateRefs<'a>,
    pub state_reads: &'a ShardedStateCache,
    pub hot_state_updates: &'a HotStateUpdates,
    /// Position summary computed at execution time (checkpoint stage), to
    /// be persisted/merklized at commit without recomputation. `None` when
    /// native position is disabled.
    pub position_state_summary: Option<&'a LedgerWithSummary<PositionStateWithSummary>>,
    pub is_reconfig: bool,
}

impl ChunkToCommit<'_> {
    pub fn len(&self) -> usize {
        self.transactions.len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn next_version(&self) -> Version {
        self.first_version + self.len() as Version
    }

    pub fn expect_last_version(&self) -> Version {
        self.next_version() - 1
    }

    pub fn result_ledger_state_with_summary(&self) -> LedgerStateWithSummary {
        let latest = StateWithSummary::new(
            self.state.latest().clone(),
            self.state_summary.latest().clone(),
        );
        let last_checkpoint = StateWithSummary::new(
            self.state.last_checkpoint().clone(),
            self.state_summary.last_checkpoint().clone(),
        );
        LedgerStateWithSummary::from_latest_and_last_checkpoint(latest, last_checkpoint)
    }
```

**File:** execution/executor/src/types/partial_state_compute_result.rs (L75-85)
```rust
    pub fn ensure_result_state_summary(&self) -> Result<&LedgerStateSummary> {
        self.ensure_state_checkpoint_output()
            .map(|out| &out.state_summary)
    }

    pub fn ensure_result_position_state_summary(
        &self,
    ) -> Result<Option<&LedgerWithSummary<PositionStateWithSummary>>> {
        self.ensure_state_checkpoint_output()
            .map(|out| out.position_state_summary.as_ref())
    }
```
