[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/block-executor/src/explicit_sync_wrapper.rs (L10-18)
```rust
// Parallel algorithms often guarantee a sequential use of certain
// data structures, or parts of the data-structures (like elements of
// a vector).  The rust compiler can not prove the safety of even
// slightly complex parallel algorithms.

/// ExplicitSyncWrapper is meant to be used in parallel algorithms
/// where we can prove that there will be no concurrent access to the
/// underlying object (or its elements).  Use with caution - only when
/// the safety can be proven.
```

**File:** aptos-move/block-executor/src/explicit_sync_wrapper.rs (L89-93)
```rust
impl<T> Drop for Guard<'_, T> {
    fn drop(&mut self) {
        self.lock.unlock();
    }
}
```
