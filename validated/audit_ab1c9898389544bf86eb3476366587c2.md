## Finding

### Title
KvOnly state-snapshot restore commits unverified state values to durable KV storage, bypassing Merkle proof checks entirely — ([File: storage/aptosdb/src/state_restore/mod.rs])

### Summary
`StateSnapshotRestore::add_chunk` in `storage/aptosdb/src/state_restore/mod.rs` branches on `StateSnapshotRestoreMode`. In `KvOnly` mode it writes the incoming `(key, value)` chunk straight into the state KV DB via `StateValueRestore::add_chunk`, and the accompanying `SparseMerkleRangeProof` argument is **never used**:

```rust
StateSnapshotRestoreMode::KvOnly => {
    let _timer = OTHER_TIMERS_SECONDS.timer_with(&["state_value_add_chunk"]);
    self.kv_restore.lock().as_mut().unwrap().add_chunk(chunk)?;
},
``` [1](#0-0) 

Compare with `TreeOnly`/`Default`, which call `verify_chunk`/`add_chunk_impl` on the `tree_restore`, cryptographically checking the chunk against `expected_root_hash` before committing anything: [2](#0-1) 

### Finding Description
`KvOnly` mode is used by the backup restore coordinator to seed the state KV DB from a state-snapshot backup at `kv_snapshot.version`, prior to replaying write sets up to `tree_snapshot.version` and then restoring the JMT tree separately with `TreeOnly` mode: [3](#0-2) [4](#0-3) 

The only integrity check performed before restoring the KV snapshot is a single top-level check that the *manifest*'s claimed `root_hash` matches the value in a `TransactionInfoWithProof` verified against the ledger: [5](#0-4) 

That check only proves the manifest's declared root hash is authentic — it does **not** prove that any individual chunk of `(key, value)` pairs downloaded from `BackupStorage` actually hashes into that root. Each chunk carries its own `SparseMerkleRangeProof` (`chunk.proof`) specifically to make that per-chunk binding, but in `KvOnly` mode this proof is discarded and never verified: [6](#0-5) 

Because the JMT tree for `kv_snapshot.version` is never rebuilt/verified in this flow (only KV bytes are written; the tree is verified later, but only at the different, later `tree_snapshot.version`, and explicitly not re-verified for the KV data: "We should not save the key value since the value is already recovered for this version" — see `aptosdb_writer.rs`), any key untouched by the write-set replay window `(kv_snapshot.version, tree_snapshot.version]` keeps whatever bytes were placed by the unverified `KvOnly` restore, forever, at that version and all following versions where it's not overwritten.

Ordinary state reads used by transaction execution (`get_state_value_by_version` / `get_state_value_with_version_by_version`) fetch raw bytes directly from the KV store without any Merkle-proof check — proof verification is only a rare, `1/10000`-sampled path used for defense-in-depth auditing, not on the hot read path: [7](#0-6) [8](#0-7) 

So if a `BackupStorage` provider (or any component along the download path in `StateSnapshotRestoreController::run_impl`, which fetches blobs and proofs from external storage) supplies a tampered chunk during `KvOnly` restore, the wrong value is durably written and will be transparently fed into VM execution as if it were the correct, cryptographically-committed state.

### Impact Explanation
A node bootstrapped or repaired via `KvOnly` backup restore (an operator-invoked but externally-supplied-data path — the manifest/blobs/proofs come from `BackupStorage`, e.g., S3/GCS/network object store) can end up executing transactions against corrupted state for any state key untouched by replay after the snapshot version. This produces VM outputs, and therefore committed write sets and state roots, that diverge from the correct network state — i.e., a hard-fork-class divergence at restore/replay time, and a durable corruption of the ledger's authoritative state store that regular execution never re-validates against a proof.

### Likelihood Explanation
This requires control over (or tampering of) the backup storage backend or transport used during `KvOnly` state-snapshot restore — realistic for third-party/self-hosted backup stores, misconfigured/non-TLS object storage, or a compromised intermediate cache. Given the manifest-level root-hash check gives a false sense of per-chunk integrity, this is not an obviously "trusted-admin-only" bug: it's a missing cryptographic check that the code otherwise clearly implements (and enforces) for `Default`/`TreeOnly` modes, but silently skips for `KvOnly`.

### Recommendation
In `StateSnapshotRestore::add_chunk`'s `KvOnly` branch, verify each chunk's `SparseMerkleRangeProof` against the `expected_root_hash` (reusing the same logic as `JellyfishMerkleRestore::verify_chunk`, without necessarily writing tree nodes) before calling `kv_restore.add_chunk`. This preserves the performance benefit of not persisting tree nodes in `KvOnly` mode while restoring the missing per-chunk authentication.

### Proof of Concept
1. Run a state-sync restore in `KvOnly` mode (`StateSnapshotRestoreOpt { restore_mode: StateSnapshotRestoreMode::KvOnly, .. }`) against a `BackupStorage` implementation you control.
2. Serve a valid `manifest.json` and valid top-level `(TransactionInfoWithProof, LedgerInfoWithSignatures)` (so `txn_info_with_proof.verify` and the `state_root_hash == manifest.root_hash` check pass).
3. For one or more `chunk` blobs referenced by the manifest, substitute a modified `(key, value)` pair (while leaving `chunk.proof` as originally issued, since it's never checked).
4. Observe `restore_utils`/`StateValueRestore::add_chunk` writes the tampered value into `state_kv_db` at `kv_snapshot.version` with no error.
5. After the coordinator continues into transaction replay and later `TreeOnly` tree restore, query the tampered key via ordinary execution/read APIs (not the sampled proof-check path) and observe the corrupted value returned without any proof failure, versus the value implied by the (separately, correctly verified) JMT tree for a version where that key is included in an accumulator-proof check.

### Citations

**File:** storage/aptosdb/src/state_restore/mod.rs (L215-218)
```rust
            StateSnapshotRestoreMode::KvOnly => {
                let _timer = OTHER_TIMERS_SECONDS.timer_with(&["state_value_add_chunk"]);
                self.kv_restore.lock().as_mut().unwrap().add_chunk(chunk)?;
            },
```

**File:** storage/aptosdb/src/state_restore/mod.rs (L219-249)
```rust
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

**File:** storage/backup/backup-cli/src/backup_types/state_snapshot/restore.rs (L123-136)
```rust
        let manifest: StateSnapshotBackup =
            self.storage.load_json_file(&self.manifest_handle).await?;
        let (txn_info_with_proof, li): (TransactionInfoWithProof, LedgerInfoWithSignatures) =
            self.storage.load_bcs_file(&manifest.proof).await?;
        txn_info_with_proof.verify(li.ledger_info(), manifest.version)?;
        let state_root_hash = txn_info_with_proof
            .transaction_info()
            .ensure_state_checkpoint_hash()?;
        ensure!(
            state_root_hash == manifest.root_hash,
            "Root hash mismatch with that in proof. root hash: {}, expected: {}",
            manifest.root_hash,
            state_root_hash,
        );
```

**File:** storage/backup/backup-cli/src/backup_types/state_snapshot/restore.rs (L191-215)
```rust
                    let blobs = Self::read_state_value(&storage, chunk.blobs.clone()).await?;
                    let proof = storage.load_bcs_file(&chunk.proof).await?;
                    Result::<_>::Ok((chunk_idx, chunk, blobs, proof))
                })
                .await?
            }
        });
        let con = self.concurrent_downloads;
        let mut futs_stream = stream::iter(futs_iter).buffered_x(con * 2, con);
        let mut start = None;
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

**File:** storage/storage-interface/src/state_store/state_view/db_state_view.rs (L26-46)
```rust
impl DbStateView {
    fn get(&self, key: &StateKey) -> StateViewResult<Option<(Version, StateValue)>> {
        if let Some(version) = self.version {
            if let Some(root_hash) = self.maybe_verify_against_state_root_hash {
                // TODO(aldenhu): sample-verify proof inside DB
                // DB doesn't support returning proofs for buffered state, so only optionally
                // verify proof.
                // TODO: support returning state proof for buffered state.
                if let Ok((value, proof)) =
                    self.db.get_state_value_with_proof_by_version(key, version)
                {
                    proof.verify(root_hash, *key.crypto_hash_ref(), value.as_ref())?;
                }
            }
            Ok(self
                .db
                .get_state_value_with_version_by_version(key, version)?)
        } else {
            Ok(None)
        }
    }
```

**File:** storage/storage-interface/src/state_store/state_summary.rs (L345-381)
```rust
    fn get_proof(
        &self,
        key: &HashValue,
        version: Version,
        root_depth: usize,
        use_hot_state: bool,
    ) -> Result<SparseMerkleProofExt> {
        if rand::random::<usize>().is_multiple_of(10000) {
            // 1 out of 10000 times, verify a full proof from the root.
            if use_hot_state {
                let (hot_value_opt, proof) =
                    self.db.get_hot_state_value_with_proof_by_version_ext(
                        *key, version, /* root_depth = */ 0,
                    )?;
                proof.verify(
                    self.state_summary.hot_root_hash()?,
                    *key,
                    hot_value_opt.as_ref(),
                )?;
                Ok(proof)
            } else {
                let (val_opt, proof) = self.db.get_state_value_with_proof_by_version_ext(
                    *key, version, /* root_depth = */ 0,
                )?;
                proof.verify(
                    self.state_summary.global_state_summary.root_hash(),
                    *key,
                    val_opt.as_ref(),
                )?;
                Ok(proof)
            }
        } else {
            Ok(self
                .db
                .get_state_proof_by_version_ext(key, version, root_depth, use_hot_state)?)
        }
    }
```
