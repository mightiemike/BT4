### Title
State-KV backup restore silently skips Merkle proof verification in `KvOnly` mode, allowing untrusted backup storage to corrupt durable ledger state - (File: `storage/aptosdb/src/state_restore/mod.rs`)

### Summary
The restore path that rebuilds a node's `state_kv_db` from a backup accepts a `SparseMerkleRangeProof` for every chunk, but when `StateSnapshotRestoreMode::KvOnly` is used, that proof is never checked against the expected (trusted) root hash before the key/value data is written to storage. This mode is exercised in the production restore-coordinator flow to seed pre-history KV state directly from an `Arc<dyn BackupStorage>` backend (e.g. S3/GCS), which is explicitly meant to be treated as untrusted and validated purely through the proof + waypoint chain. Skipping the proof check breaks that trust boundary and lets a malicious/compromised backup source inject arbitrary state values into durable storage.

### Finding Description
`StateSnapshotRestore::add_chunk` dispatches on `restore_mode`: [1](#0-0) 

In `Default` mode the code explicitly documents and enforces "verify proof -> write kv -> write merkle" by calling `tree_restore.verify_chunk(...)` before `kv_restore.add_chunk(chunk)`. In `TreeOnly` mode, `add_chunk_impl` also calls `verify_chunk` internally. But in `KvOnly` mode, only `self.kv_restore.lock().as_mut().unwrap().add_chunk(chunk)?;` is invoked — the `proof: SparseMerkleRangeProof` argument passed into `add_chunk` is never read in that branch.

The underlying `StateValueRestore::add_chunk` performs no cryptographic check at all — it simply loads write progress, skips already-processed keys, and calls `self.db.write_kv_batch(...)` directly: [2](#0-1) 

`KvOnly` is not a test-only or dead code path — it is used in the production `RestoreCoordinator` to seed the state-KV database for the range between `ledger_history_start_version` and the tree-snapshot version, before transactions are replayed forward: [3](#0-2) 

The chunk-download loop that drives this restore pulls both the state-value blobs and their proof from the same untrusted `Arc<dyn BackupStorage>` backend and feeds them straight to `add_chunk`, regardless of mode: [4](#0-3) 

Because `KvOnly`'s `add_chunk` discards the proof, the `expected_root_hash` (derived from the trusted `GlobalRestoreOptions`/waypoints/epoch history, the entire reason backup+restore is safe against an untrusted storage backend) is never used to validate the KV blobs written in this phase.

### Impact Explanation
An attacker who controls, compromises, or can man-in-the-middle the backup storage backend (this is precisely the threat model the proof-verification design in `storage/backup/backup-cli` is meant to defend against — see the dedicated `Verify` run mode in `storage/backup/backup-cli/src/coordinators/verify.rs` and `storage/backup/backup-cli/src/utils/mod.rs`) can serve arbitrary key/value pairs during the KV-only restore phase with zero cryptographic validation. Any state key that is written here but never subsequently touched by the transaction-replay phase (`TransactionRestoreBatchController` in phase 1.b, which only overwrites keys actually modified by replayed transactions) permanently retains the attacker-supplied, unverified value in the node's durable `state_kv_db`. This silently corrupts committed ledger data on the restoring node: subsequent reads, API responses, and indexer output built from that KV store for the affected keys diverge from the canonical chain state, with no error surfaced anywhere in the restore pipeline (`finish()`/`kv_finish()` also perform no root-hash check). This is a direct violation of the "committed state must match the correct chain result" and "restore paths must preserve deterministic proof binding" integrity invariants.

### Likelihood Explanation
Any node operator using the standard backup/restore coordinator against a backup store they do not fully control (cloud buckets, third-party backup mirrors, or any channel subject to MITM without additional out-of-band integrity checks) is exposed. This is exactly the scenario the proof-and-waypoint verification exists to protect against, so the missing check in one specific restore mode defeats the design's core security property with no additional privilege required from the attacker beyond control of the backup artifact.

### Recommendation
In `StateSnapshotRestore::add_chunk`'s `KvOnly` branch, call `tree_restore.verify_chunk(...)` (or an equivalent hash-verification routine) against the supplied `proof` and `expected_root_hash` before writing to `kv_restore`, mirroring the `Default` mode's "verify proof -> write kv" ordering, even though the Merkle tree itself is not being persisted in this mode.

### Proof of Concept
1. Set up a `RestoreCoordinator` restoring from an attacker-controlled `BackupStorage` implementation (e.g. a malicious `LocalFs`/S3 backend), with a valid target ledger info/waypoint chain fetched normally (so `expected_root_hash` is correctly trusted for the tree-snapshot phase).
2. For the KV-snapshot manifest consumed in phase 1.a (`kv_snapshot.version < tree_snapshot.version`), serve tampered `(StateKey, StateValue)` blobs alongside an unrelated/stale `SparseMerkleRangeProof` (or any junk proof bytes that would fail verification against `expected_root_hash`).
3. Observe that `StateSnapshotRestoreController` with `StateSnapshotRestoreMode::KvOnly` completes successfully (`storage/aptosdb/src/state_restore/mod.rs:213-218` never validates the proof), and the tampered values are written to `state_kv_db` via `StateValueRestore::add_chunk`.
4. Complete the remaining phases normally; keys not touched by transactions replayed in phase 1.b/2.b retain the tampered values, while `db.get_root_hash` at the final tree-snapshot version still matches (since the JMT itself is restored/verified separately and doesn't re-validate KV byte contents against stored hashes at read time), leaving the corruption undetected.

### Citations

**File:** storage/aptosdb/src/state_restore/mod.rs (L73-112)
```rust
    pub fn add_chunk(&mut self, mut chunk: Vec<(K, V)>) -> Result<()> {
        // load progress
        let progress_opt = self.db.get_progress(self.version)?;

        // skip overlaps
        if let Some(progress) = progress_opt {
            let idx = chunk
                .iter()
                .position(|(k, _v)| CryptoHash::hash(k) > progress.key_hash)
                .unwrap_or(chunk.len());
            chunk = chunk.split_off(idx);
        }

        // quit if all skipped
        if chunk.is_empty() {
            return Ok(());
        }

        // save
        let mut usage = progress_opt.map_or(StateStorageUsage::zero(), |p| p.usage);
        let (last_key, _last_value) = chunk.last().unwrap();
        let last_key_hash = CryptoHash::hash(last_key);

        // In case of TreeOnly Restore, we only restore the usage of KV without actually writing KV into DB
        for (k, v) in chunk.iter() {
            usage.add_item(k.key_size() + v.value_size());
        }

        // prepare the sharded kv batch
        let kv_batch: StateValueBatch<K, Option<V>> = chunk
            .into_iter()
            .map(|(k, v)| ((k, self.version), Some(v)))
            .collect();

        self.db.write_kv_batch(
            self.version,
            &kv_batch,
            StateSnapshotProgress::new(last_key_hash, usage),
        )
    }
```

**File:** storage/aptosdb/src/state_restore/mod.rs (L213-250)
```rust
    fn add_chunk(&mut self, chunk: Vec<(K, V)>, proof: SparseMerkleRangeProof) -> Result<()> {
        match self.restore_mode {
            StateSnapshotRestoreMode::KvOnly => {
                let _timer = OTHER_TIMERS_SECONDS.timer_with(&["state_value_add_chunk"]);
                self.kv_restore.lock().as_mut().unwrap().add_chunk(chunk)?;
            },
            StateSnapshotRestoreMode::TreeOnly => {
                let _timer = OTHER_TIMERS_SECONDS.timer_with(&["jmt_add_chunk"]);
                self.tree_restore
                    .lock()
                    .as_mut()
                    .unwrap()
                    .add_chunk_impl(chunk.iter().map(|(k, v)| (k, v.hash())).collect(), proof)?;
            },
            StateSnapshotRestoreMode::Default => {
                // Sequence: verify proof -> write state_kv_db -> write state_merkle_db.
                // This keeps state_kv_db at or ahead of state_merkle_db on disk at every
                // crash point. Were merkle ever ahead, the resume path (which feeds chunks
                // from min(kv_progress, tree_progress)) would land bytes in (kv_progress,
                // tree_progress] that the tree side skips and therefore does not re-verify.
                {
                    let _timer = OTHER_TIMERS_SECONDS.timer_with(&["jmt_verify_chunk"]);
                    self.tree_restore
                        .lock()
                        .as_mut()
                        .unwrap()
                        .verify_chunk(chunk.iter().map(|(k, v)| (k, v.hash())).collect(), proof)?;
                }
                {
                    let _timer = OTHER_TIMERS_SECONDS.timer_with(&["state_value_add_chunk"]);
                    self.kv_restore.lock().as_mut().unwrap().add_chunk(chunk)?;
                }
                {
                    let _timer = OTHER_TIMERS_SECONDS.timer_with(&["jmt_commit_chunk"]);
                    self.tree_restore.lock().as_mut().unwrap().commit_chunk()?;
                }
            },
        }
```

**File:** storage/backup/backup-cli/src/coordinators/restore.rs (L242-260)
```rust
            // phase 1.a: restore the kv snapshot
            if kv_snapshot.is_some() {
                let kv_snapshot = kv_snapshot.clone().unwrap();
                info!("Start restoring KV snapshot at {}", kv_snapshot.version);

                StateSnapshotRestoreController::new(
                    StateSnapshotRestoreOpt {
                        manifest_handle: kv_snapshot.manifest,
                        version: kv_snapshot.version,
                        validate_modules: false,
                        restore_mode: StateSnapshotRestoreMode::KvOnly,
                    },
                    self.global_opt.clone(),
                    Arc::clone(&self.storage),
                    epoch_history.clone(),
                )
                .run()
                .await?;
            }
```

**File:** storage/backup/backup-cli/src/backup_types/state_snapshot/restore.rs (L201-215)
```rust
        while let Some((chunk_idx, chunk, mut blobs, proof)) = futs_stream.try_next().await? {
            start = start.or_else(|| Some(Instant::now()));
            let _timer = OTHER_TIMERS_SECONDS.timer_with(&["add_state_chunk"]);
            let receiver = receiver.clone();
            if self.validate_modules {
                blobs = tokio::task::spawn_blocking(move || {
                    Self::validate_modules(&blobs);
                    blobs
                })
                .await?;
            }
            tokio::task::spawn_blocking(move || {
                receiver.lock().as_mut().unwrap().add_chunk(blobs, proof)
            })
            .await??;
```
