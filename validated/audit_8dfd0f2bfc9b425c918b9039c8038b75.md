[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/block-executor/src/captured_reads.rs (L134-136)
```rust
    fn value_size(v: &TriompheArc<V>) -> Option<u64> {
        v.bytes().map(|bytes| bytes.len() as u64)
    }
```

**File:** types/src/block_executor/value.rs (L41-45)
```rust
    RawFromStorage(Arc<V>),
    /// Storage value that ran exchange or a write. The layout if set indicates
    /// there are delayed fields inside and [`None`] otherwise.
    Exchanged(Arc<V>, Option<Arc<MoveTypeLayout>>),
}
```

**File:** types/src/block_executor/value.rs (L102-109)
```rust
    fn bytes_len(&self) -> Option<usize> {
        fail_point!("value_with_layout_bytes_len", |_| { Some(10) });
        match self {
            ValueWithLayout::RawFromStorage(value) | ValueWithLayout::Exchanged(value, _) => {
                value.bytes().map(|b| b.len())
            },
        }
    }
```
