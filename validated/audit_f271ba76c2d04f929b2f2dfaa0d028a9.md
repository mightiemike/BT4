[1](#0-0) [2](#0-1) [3](#0-2) [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** storage/aptosdb/src/state_store/state_snapshot_committer.rs (L39-44)
```rust
    let previous_epoch_ending_version = state_db
        .ledger_db
        .metadata_db()
        .get_previous_epoch_ending(version)
        .unwrap()
        .map(|(v, _e)| v);
```

**File:** storage/aptosdb/src/state_store/state_snapshot_committer.rs (L91-100)
```rust
            let (_root, _leaf_count, top_levels_batch, batches_for_shards) = state_db
                .hot_state_merkle_db
                .merklize_snapshot(
                    base_version,
                    version,
                    last_hot,
                    snap_hot,
                    hot_updates.try_into().expect("Must be 16 shards."),
                    previous_epoch_ending_version,
                )
```

**File:** storage/aptosdb/src/sharded_jmt_merkle_db.rs (L337-359)
```rust
        let (shard_root_nodes, sharded_batches) = (0..16)
            .map(|shard_id| {
                self.merklize_value_set_for_shard(
                    shard_id,
                    sharded_value_set[shard_id].clone(),
                    /*node_hashes=*/ None,
                    version,
                    base_version,
                    base_version,
                    previous_epoch_ending_version,
                )
                .unwrap()
            })
            .collect::<Vec<_>>()
            .into_iter()
            .unzip();

        let (root_hash, _leaf_count, top_levels_batch) = self.calculate_top_levels(
            shard_root_nodes,
            version,
            base_version,
            previous_epoch_ending_version,
        )?;
```

**File:** storage/aptosdb/src/sharded_jmt_merkle_db.rs (L364-415)
```rust
    /// Calculates db updates for nodes in shard `shard_id`.
    pub fn merklize_value_set_for_shard(
        &self,
        shard_id: usize,
        value_set: Vec<(HashValue, Option<&(HashValue, StateKey)>)>,
        node_hashes: Option<&HashMap<NibblePath, HashValue>>,
        version: Version,
        base_version: Option<Version>,
        shard_persisted_version: Option<Version>,
        previous_epoch_ending_version: Option<Version>,
    ) -> Result<(Node, RawBatch)> {
        if let Some(shard_persisted_version) = shard_persisted_version {
            assert!(shard_persisted_version <= base_version.expect("Must have base version."));
        }

        let (shard_root_node, tree_update_batch) = {
            let _timer =
                OTHER_TIMERS_SECONDS.timer_with(&[&format!("{}__jmt_update", self.db_tag)]);

            self.batch_put_value_set_for_shard(
                shard_id,
                value_set,
                node_hashes,
                shard_persisted_version,
                version,
            )
        }?;

        if self.cache_enabled() {
            self.version_caches
                .get(&Some(shard_id))
                .unwrap()
                .add_version(
                    version,
                    tree_update_batch
                        .node_batch
                        .iter()
                        .flatten()
                        .cloned()
                        .collect(),
                );
        }

        let batch = self.create_jmt_commit_batch_for_shard(
            version,
            Some(shard_id),
            &tree_update_batch,
            previous_epoch_ending_version,
        )?;

        Ok((shard_root_node, batch))
    }
```

**File:** storage/aptosdb/src/sharded_jmt_merkle_db.rs (L417-450)
```rust
    /// Calculates db updates for non-sharded nodes at top levels.
    pub fn calculate_top_levels(
        &self,
        shard_root_nodes: Vec<Node>,
        version: Version,
        base_version: Option<Version>,
        previous_epoch_ending_version: Option<Version>,
    ) -> Result<(HashValue, usize, RawBatch)> {
        assert!(shard_root_nodes.len() == 16);

        let (root_hash, leaf_count, tree_update_batch) = JellyfishMerkleTree::new(self)
            .put_top_levels_nodes(shard_root_nodes, base_version, version)?;

        if self.cache_enabled() {
            self.version_caches.get(&None).unwrap().add_version(
                version,
                tree_update_batch
                    .node_batch
                    .iter()
                    .flatten()
                    .cloned()
                    .collect(),
            );
        }

        let batch = self.create_jmt_commit_batch_for_shard(
            version,
            None,
            &tree_update_batch,
            previous_epoch_ending_version,
        )?;

        Ok((root_hash, leaf_count, batch.into_raw_batch(self.db(None))?))
    }
```
