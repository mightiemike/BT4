### Title
KvOnly state-snapshot restore writes unverified state values into `state_kv_db`, decoupling stored raw values from the Merkle-proof-verified root - (File: `storage/aptosdb/src/state_restore/mod.rs`)

### Summary
`StateSnapshotRestore::add_chunk` has three modes. In `StateSnapshotRestoreMode::KvOnly`, it writes the supplied `(K, V)` chunk directly into the state-value store via `StateValueRestore::add_chunk`, **without ever calling `verify_chunk`/`verify` against the accompanying `SparseMerkleRangeProof`**: [1](#0-0) 

This is used in the backup restore coordinator's "phase 1.a", where a KV-only snapshot is restored from a backup manifest before the corresponding Jellyfish Merkle tree snapshot is separately restored (and verified) via `TreeOnly` mode from a *different* manifest/download: [2](#0-1) [3](#0-2) 

### Finding Description
The `Default` restore mode couples writes correctly: it verifies the `SparseMerkleRangeProof` against `v.hash()` for each chunk *before* writing the same chunk's raw bytes to `state_kv_db`, guaranteeing the stored raw value hashes to the value that was cryptographically bound to the JMT proof and, transitively, to the ledger's signed root hash: [4](#0-3) 

`KvOnly` mode breaks this coupling: `StateValueRestore::add_chunk` only tracks `previous_key_hash`/usage bookkeeping and blindly persists whatever raw values are handed to it - there is no hash check of the value contents at all: [5](#0-4) 

Meanwhile, the tree side is verified independently and later, from a separate manifest download (`tree_snapshot`), using its own re-downloaded blobs' hashes against the JMT proof - never touching or cross-checking the bytes already committed to `state_kv_db` by the earlier `KvOnly` pass: [6](#0-5) 

Any keys not subsequently overwritten by the (proof-verified) transaction replay between `kv_snapshot.version + 1` and `tree_snapshot.version` retain whatever bytes the `KvOnly` pass wrote for them, unverified against any accumulator/tree root. The JMT leaf for that key still asserts (from the tree-side restore) that the value at that version hashes to a specific value, but nothing in this code path ever re-confirms that the bytes actually stored in `state_kv_db` produce that hash. This is exactly the pattern flagged in the external report - a state-mutating step (here, "initializing" `state_kv_db`) that skips the integrity check that a sibling code path performs, relying on the deployer/operator to always feed the two restore stages from consistent, honest sources.

### Impact Explanation
If the two downloads (KV-only manifest and tree manifest) used to bootstrap/restore a node are not identical (e.g., because the backup storage source serving them is tampered with, compromised, or a MITM'd network fetch), a node can end up with `state_kv_db` values that do not correspond to the state root it advertises, permanently corrupting durable ledger data for that node. Authenticated APIs and state-view reads (e.g. `get_state_value`, proof-serving endpoints) built on top of this node's storage would then return incorrect values while the node still believes/reports a root hash consistent with the real chain - i.e., an authenticated state-view output bound to the wrong underlying object content. This matches the "Committed state that differs from the correct VM result or corrupts durable ledger data" and "Authenticated API...output bound to the wrong version, object, or proof context" impact classes.

### Likelihood Explanation
This path is only reachable through the backup/restore/fast-bootstrap flow, and only when the KV-only manifest source is untrustworthy or tampered relative to the (separately verified) tree snapshot source - it is not exploitable by an ordinary unprivileged transaction sender against a running validator's live execution path. It requires the restoring node to fetch state data from a backup storage endpoint (which can be a public bucket/URL, not necessarily the node operator's own trusted infrastructure) that is compromised or spoofed for the KV-only phase while the tree phase is fetched intact (or vice versa is inconsequential since only KV bytes lack verification). Given restore/backup tooling is typically used by node operators, exploitation likelihood is limited to scenarios where the backup source itself is compromised, but the code contains no compensating check even in that scenario, which is the local root cause being reported.

### Recommendation
In `StateSnapshotRestoreMode::KvOnly`, do not skip value verification: either require and check a `SparseMerkleRangeProof` (or otherwise-provided root-hash-derived leaf hashes) against the chunk's `v.hash()` before writing to `state_kv_db`, or perform a post-hoc reconciliation pass after the tree-only restore that recomputes and verifies stored KV bytes against the already-verified JMT leaves before the DB is marked bootstrapped/servable. At minimum, `finish()` for `KvOnly`/`Default` restore should assert that every persisted value hashes to the value recorded by the corresponding tree leaf before allowing the restored DB to be used to serve state or proofs.

### Proof of Concept
1. An attacker controls (or can tamper in-flight with) the backup storage location that serves the *KV-only* manifest referenced by `RestoreCoordinator::run_impl` phase 1.a (`storage/backup/backup-cli/src/coordinators/restore.rs:242-260`), while the tree-snapshot manifest fetched later in phase 2.a is left untouched/legitimate.
2. During restore, `StateSnapshotRestoreController::run_impl` (`storage/backup/backup-cli/src/backup_types/state_snapshot/restore.rs`) downloads the tampered KV blobs and calls `receiver.add_chunk(blobs, proof)`, which for `KvOnly` mode routes to `StateSnapshotRestore::add_chunk` → `StateValueRestore::add_chunk`, writing the attacker-controlled bytes straight into `state_kv_db` with zero proof/hash verification (`storage/aptosdb/src/state_restore/mod.rs:213-218, 73-112`).
3. Phase 1.b replays only the transactions between `kv_snapshot.version+1` and `tree_snapshot.version`; any state key untouched by that replay window keeps the attacker's injected value.
4. Phase 2.a restores the tree in `TreeOnly` mode from the legitimate tree manifest, verifying JMT structure/hashes from its own re-downloaded blobs - this never reads back or re-hashes the bytes already written to `state_kv_db` in step 2, so the corruption is never detected.
5. The resulting node serves a state root that matches the honest chain while returning corrupted values for the affected keys through its state-view/API layer.

Note: I could not fully load `storage/backup/backup-cli/src/backup_types/state_snapshot/manifest.rs` or the remainder of `restore.rs` (tool errors on the final iteration) to confirm whether the `StateSnapshotBackup`/chunk manifest carries any independent value-digest field that might mitigate this outside the code already cited; this should be verified before treating the finding as fully confirmed.

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

**File:** storage/aptosdb/src/state_restore/mod.rs (L213-253)
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

        Ok(())
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

**File:** storage/backup/backup-cli/src/coordinators/restore.rs (L316-346)
```rust
            if !tree_completed {
                // For boostrap DB to latest version, we want to use default mode
                let restore_mode_opt = if db_next_version > 0 {
                    if replay_all_mode {
                        None // the restore should already been done in the replay_all mode
                    } else {
                        Some(StateSnapshotRestoreMode::TreeOnly)
                    }
                } else {
                    Some(StateSnapshotRestoreMode::Default)
                };

                if let Some(restore_mode) = restore_mode_opt {
                    info!(
                        "Start restoring tree snapshot at {} with db_next_version {}",
                        tree_snapshot.version, db_next_version
                    );
                    StateSnapshotRestoreController::new(
                        StateSnapshotRestoreOpt {
                            manifest_handle: tree_snapshot.manifest.clone(),
                            version: tree_snapshot.version,
                            validate_modules: false,
                            restore_mode,
                        },
                        self.global_opt.clone(),
                        Arc::clone(&self.storage),
                        epoch_history.clone(),
                    )
                    .run()
                    .await?;
                }
```
