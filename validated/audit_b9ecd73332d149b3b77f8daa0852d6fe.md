[1](#0-0) [2](#0-1)

### Citations

**File:** storage/aptosdb/src/db/aptosdb_native_position.rs (L255-266)
```rust
        let mut pending_leaf_updates: HashMap<HashValue, PositionSlot> = HashMap::new();
        for write_set in &write_sets {
            for (key, op) in write_set.native_position_iter() {
                let maybe_value = op.as_write_op().as_state_value_opt().cloned();
                let value_hash = maybe_value.as_ref().map(StateValue::hash);
                pending_leaf_updates.insert(key.hash(), PositionSlot {
                    state_key: key.clone(),
                    value_hash,
                    value: None,
                });
            }
        }
```
