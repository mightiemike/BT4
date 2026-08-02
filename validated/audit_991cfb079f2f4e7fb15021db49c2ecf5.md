[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** storage/aptosdb/src/pruner/state_merkle_pruner/state_merkle_pruner_manager.rs (L110-138)
```rust
    pub fn new(
        state_merkle_db: Arc<D>,
        state_merkle_pruner_config: StateMerklePrunerConfig,
    ) -> Self {
        let pruner_worker = if state_merkle_pruner_config.enable {
            Some(Self::init_pruner(
                Arc::clone(&state_merkle_db),
                state_merkle_pruner_config,
            ))
        } else {
            None
        };

        let min_readable_version =
            pruner_utils::get_state_merkle_pruner_progress::<M>(&state_merkle_db)
                .expect("Must succeed.");

        PRUNER_VERSIONS
            .with_label_values(&[M::name(), "min_readable"])
            .set(min_readable_version as i64);

        Self {
            state_merkle_db,
            prune_window: state_merkle_pruner_config.prune_window,
            pruner_worker,
            min_readable_version: AtomicVersion::new(min_readable_version),
            _phantom: PhantomData,
        }
    }
```

**File:** storage/aptosdb/src/db/aptosdb_test.rs (L135-152)
```rust
#[test]
fn test_error_if_version_pruned() {
    let tmp_dir = TempPath::new();
    let db = AptosDB::new_for_test(&tmp_dir);
    db.state_store
        .state_db
        .state_pruner
        .state_merkle_pruner
        .save_min_readable_version(5)
        .unwrap();
    db.ledger_pruner.save_min_readable_version(10).unwrap();
    assert_eq!(
        db.error_if_state_merkle_pruned("State", 4)
            .unwrap_err()
            .to_string(),
        "AptosDB Other Error: Version 4 is not epoch ending."
    );
    assert!(db.error_if_state_merkle_pruned("State", 5).is_ok());
```

**File:** storage/backup/backup-cli/src/coordinators/verify.rs (L84-141)
```rust
    async fn run_impl(self) -> Result<()> {
        let metadata_view = metadata::cache::sync_and_load(
            &self.metadata_cache_opt,
            Arc::clone(&self.storage),
            self.concurrent_downloads,
        )
        .await?;
        let ver_max = Version::MAX;
        let state_snapshot =
            metadata_view.select_state_snapshot(self.state_snapshot_before_version)?;
        let transactions =
            metadata_view.select_transaction_backups(self.start_version, self.end_version)?;
        let epoch_endings = metadata_view.select_epoch_ending_backups(ver_max)?;

        let global_opt = GlobalRestoreOptions {
            target_version: ver_max,
            trusted_waypoints: Arc::new(self.trusted_waypoints_opt.verify()?),
            run_mode: Arc::new(RestoreRunMode::Verify),
            concurrent_downloads: self.concurrent_downloads,
            replay_concurrency_level: 0, // won't replay, doesn't matter
        };

        let epoch_history = if self.skip_epoch_endings {
            None
        } else {
            Some(Arc::new(
                EpochHistoryRestoreController::new(
                    epoch_endings
                        .into_iter()
                        .map(|backup| backup.manifest)
                        .collect(),
                    global_opt.clone(),
                    self.storage.clone(),
                )
                .run()
                .await?,
            ))
        };

        if let Some(backup) = state_snapshot {
            info!(
                epoch = backup.epoch,
                version = backup.version,
                "State snapshot selected for verification."
            );
            StateSnapshotRestoreController::new(
                StateSnapshotRestoreOpt {
                    manifest_handle: backup.manifest,
                    version: backup.version,
                    validate_modules: self.validate_modules,
                    restore_mode: StateSnapshotRestoreMode::Default,
                },
                global_opt.clone(),
                Arc::clone(&self.storage),
                epoch_history.clone(),
            )
            .run()
            .await?;
```

**File:** storage/aptosdb/src/state_restore/restore_test.rs (L182-217)
```rust
        let (db, version) = init_mock_store(&all.clone().into_values().collect());
        let tree = JellyfishMerkleTree::new(&db);
        let expected_root_hash = tree.get_root_hash(version).unwrap();
        let batch1: Vec<_> = all.clone().into_iter().take(batch1_size).collect();

        let restore_db = Arc::new(MockSnapshotStore::default());
        {
            let mut restore =
                StateSnapshotRestore::new(&restore_db, &restore_db,  version, expected_root_hash, true /* async_commit */, StateSnapshotRestoreMode::Default).unwrap();
            let proof = tree
                .get_range_proof(batch1.last().map(|(key, _value)| *key).unwrap(), version)
                .unwrap();
            restore.add_chunk(batch1.into_iter().map(|(_, kv)| kv).collect(), proof).unwrap();
            // Do not call `finish`.
        }

        {
            let remaining_accounts: Vec<_> = all.clone().into_iter().skip(batch1_size - overlap_size).collect();

            let mut restore =
                StateSnapshotRestore::new(&restore_db, &restore_db,  version, expected_root_hash, true /* async commit */, StateSnapshotRestoreMode::Default ).unwrap();
            let proof = tree
                .get_range_proof(
                    remaining_accounts.last().map(|(h, _)| *h).unwrap(),
                    version,
                )
                .unwrap();
            restore.add_chunk(remaining_accounts.into_iter().
                map(|(_, kv)| kv)
                              .collect()
                              , proof).unwrap();
            restore.finish().unwrap();
        }

        assert_success(&restore_db, expected_root_hash, &all, version);
    }
```
