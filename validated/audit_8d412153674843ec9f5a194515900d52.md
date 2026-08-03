[1](#0-0) [2](#0-1)

### Citations

**File:** storage/aptosdb/src/common.rs (L351-355)
```rust
pub trait BufferedStateExtras<P, S>: Send + 'static {
    type ChunkInput;
    fn absorb_chunk(&mut self, input: Self::ChunkInput, checkpoint_advanced: bool);
    fn build_payload(&mut self, snapshot: S) -> P;
}
```

**File:** storage/aptosdb/src/state_store/buffered_state.rs (L30-35)
```rust
pub type BufferedState = crate::common::BufferedState<
    LedgerStateWithSummary,
    StateWithSummary,
    SnapshotToCommit,
    HotStateAccumulator,
>;
```
