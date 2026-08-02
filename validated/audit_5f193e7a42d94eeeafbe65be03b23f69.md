[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/aptos-vm/src/move_vm_ext/session/mod.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```

**File:** aptos-move/framework/natives/src/aggregator_natives/context.rs (L190-198)
```rust
        // Optimization disabled: the value is a concrete u128 tracked in place.
        for (id, value) in values {
            let change = if is_write(&id) {
                AggregatorChangeV1::Write(value)
            } else {
                AggregatorChangeV1::MaterializedDelta(value)
            };
            aggregator_v1_changes.insert(id.0, change);
        }
```

**File:** aptos-move/framework/natives/src/aggregator_natives/context.rs (L342-356)
```rust
        // Aggregators touched directly via `aggregator_v1_data` end the transaction in a delta
        // state, so `into_change_set` materializes them against the resolver (600: 100 + 100,
        // 700: 200 + 200).
        assert_matches!(
            aggregator_v1_changes
                .get(&aggregator_v1_state_key_for_test(600))
                .unwrap(),
            AggregatorChangeV1::MaterializedDelta(200)
        );
        assert_matches!(
            aggregator_v1_changes
                .get(&aggregator_v1_state_key_for_test(700))
                .unwrap(),
            AggregatorChangeV1::MaterializedDelta(400)
        );
```
