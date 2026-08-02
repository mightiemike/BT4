[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** storage/storage-interface/src/state_store/state.rs (L300-319)
```rust
                    let new_usage = Self::usage_delta_for_shard(cache, overlay, batched_updates);
                    (((new_layer, new_metadata), new_usage), shard_updates)
                },
            )
            .unzip();
        let shards = Arc::new(shards.try_into().expect("Known to be 16 shards."));
        let new_metadata = new_metadata.try_into().expect("Known to be 16 shards.");
        let usage = self.update_usage(usage_delta_per_shard);
        let hot_state_updates = hot_state_updates
            .try_into()
            .expect("Known to be 16 shards.");

        // TODO(HotState): extract and pass new hot state onchain config if needed.
        Ok((
            State::new_with_updates(
                batched_updates.last_version(),
                shards,
                new_metadata,
                usage,
                self.hot_state_config,
```

**File:** storage/storage-interface/src/state_store/state.rs (L923-932)
```rust
    #[test]
    fn test_update_usage_sums_deltas() {
        let state = State::new_at_version(Some(0), StateStorageUsage::new(10, 1000), TEST_CONFIG);
        let mut deltas = vec![(0i64, 0i64); NUM_STATE_SHARDS];
        deltas[0] = (3, 100);
        deltas[5] = (-1, -40);
        let usage = state.update_usage(deltas);
        assert_eq!(usage.items(), 12);
        assert_eq!(usage.bytes(), 1060);
    }
```
