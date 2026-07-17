[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** core/primitives/src/state.rs (L107-110)
```rust
pub enum FlatStateValue {
    Ref(ValueRef) = 0,
    Inlined(Vec<u8>) = 1,
}
```

**File:** core/primitives/src/state.rs (L113-114)
```rust
    pub const INLINE_DISK_VALUE_THRESHOLD: usize =
        near_primitives_core::config::INLINE_DISK_VALUE_THRESHOLD;
```

**File:** core/store/src/flat/delta.rs (L116-117)
```rust
            let flat_state_value = last_change.as_ref().map(|value| FlatStateValue::on_disk(value));
            delta.insert(key, flat_state_value);
```
