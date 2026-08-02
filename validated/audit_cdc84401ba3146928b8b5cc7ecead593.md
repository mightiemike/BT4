[1](#0-0)

### Citations

**File:** storage/aptosdb/src/db/aptosdb_native_position.rs (L30-47)
```rust
pub struct PositionBundle {
    pub kv_db: Arc<PositionDb>,
    pub merkle_db: Arc<PositionMerkleDb>,
    /// Pruner managers (value + merkle), the analog of main state's
    /// `StatePruner`. `None` in readonly mode. Held as `Arc` so the
    /// position merkle batch committer shares it; the value pruner is
    /// driven from `commit_native_position`, the merkle pruners from the
    /// committer, and all are re-activated on restart from `open_internal`.
    pub(crate) position_pruner: Option<Arc<PositionPruner>>,
    /// `None` in readonly mode.
    pub(crate) state_store: Option<Arc<PositionStateStore>>,
    /// Latest persisted in-memory snapshot — the base the in-memory
    /// chain rebases onto each chunk (SMT freeze base + proof
    /// version). Advanced by the merkle batch committer as snapshots
    /// persist, so the proof base tracks the JMT forward and the
    /// in-memory tree sheds nodes below it. `None` in readonly mode.
    pub(crate) persisted: Option<PositionPersistedState>,
}
```
