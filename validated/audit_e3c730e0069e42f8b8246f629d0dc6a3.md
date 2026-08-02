### Title
Unverified state values from `KvOnly` snapshot restore are never cross-checked against the verified Merkle root, allowing corrupted committed state - ([File: storage/aptosdb/src/state_restore/mod.rs])

### Summary
The Aptos backup-restore pipeline downloads state-snapshot data from a `BackupStorage` backend that is explicitly *not* trusted — that is the entire reason snapshots ship with a Merkle proof and a ledger-info-signed root hash for the operator to verify against. However, `StateSnapshotRestore::add_chunk` skips this verification entirely when `StateSnapshotRestoreMode::KvOnly` is used, and the code path that later restores the trusted Merkle tree (`TreeOnly`) never cross-checks its verified leaf hashes against what was actually written to `state_kv_db` by the earlier unverified `KvOnly` pass. This lets values supplied by a malicious/compromised backup storage provider be committed into `state_kv_db` while the accompanying, cryptographically-verified state root continues to "vouch" for different (correct) values.

### Finding Description
In `storage/aptosdb/src/state_restore/mod.rs`, `StateSnapshotRestore::add_chunk` dispatches on `restore_mode`: [1](#0-0) 

- `KvOnly`: calls `self.kv_restore...add_chunk(chunk)?` directly — no call to `tree_restore.verify_chunk`/`add_chunk_impl`, i.e. the `SparseMerkleRangeProof` parameter is accepted but **discarded**, and the raw `(key, value)` pairs are written straight to `state_kv_db` with no relation to the `expected_root_hash` the restore was constructed with.
- `TreeOnly`: only ever consumes `v.hash()` from the chunk to rebuild JMT nodes; it never persists the value bytes to `state_kv_db` and never reads back what is already stored there to compare hashes.
- `Default`: is the only mode that actually verifies proof-then-write, per the code comment describing that ordering invariant.

This `KvOnly`/`TreeOnly` split is exactly what `RestoreCoordinator::run_impl` uses in production restores: [2](#0-1) 

Phase 1.a restores an older "kv_snapshot" at `kv_snapshot.version` using `StateSnapshotRestoreMode::KvOnly` — **unverified**. Phase 1.b then replays only the transactions between `kv_snapshot.version+1` and `tree_snapshot.version`, which updates `state_kv_db` **only for keys touched by those transactions' write sets**: [3](#0-2) 

Phase 2.a then restores the tree at `tree_snapshot.version` using `StateSnapshotRestoreMode::TreeOnly`: [4](#0-3) 

The overall `manifest.root_hash`/`state_root_hash` used to construct each `StateSnapshotRestore` is verified against the signed `LedgerInfo` before any chunk is processed: [5](#0-4) 

but that verification only guarantees the **tree structure built during the `TreeOnly` pass** is consistent with the signed root — it says nothing about the bytes already sitting in `state_kv_db` from the earlier, unverified `KvOnly` pass. For any state key that is present at `kv_snapshot.version` and is **never touched** by a write set in the intervening transaction range (a very common case — most accounts/resources are untouched across a snapshot interval), the value actually served out of `state_kv_db` at the end of restore is whatever the (adversarial) backup storage supplied during the `KvOnly` phase, while the JMT built by `TreeOnly` for the same key is built from a *separately downloaded* chunk of the tree_snapshot manifest and only checks `v.hash()` against the proof, never comparing against the value on disk.

Consequently, `db.get_root_hash(tree_snapshot.version)` can correctly equal the ledger-info-verified root while `state_kv_db.get_state_value(key, ...)` returns bytes with a different hash than the one committed in that verified root. Since transaction execution (VM) reads state through `state_kv_db`/`StateView`, not by re-deriving values from Merkle proofs on every read, subsequent transaction replay/execution against the restored DB executes against silently corrupted state.

### Impact Explanation
This breaks the fundamental invariant that "committed state must match the value bound by the accumulator/Merkle root" (the exact class of proof-and-storage-pivot invariant called out in scope). A node whose operator restores from a compromised or malicious backup bucket (a scenario the proof-carrying backup format is specifically designed to defend against) ends up with a `state_kv_db` containing attacker-chosen values for untouched keys, even though its Merkle root and signed ledger info both verify successfully. Any node built this way will diverge from the correct VM/ledger state and will produce wrong execution results for subsequent transactions that read the corrupted keys — a hard, silent state-commitment corruption that defeats the entire point of proof-based backup verification. This is High/Critical because it undermines the trust model of the backup/restore subsystem (the guarantee is "you can safely restore from an untrusted bucket"), and the corruption is undetectable by hash/root checks alone.

### Likelihood Explanation
The `KvOnly`/`TreeOnly` split is not test-only code — it is the exact mode combination used by the production `RestoreCoordinator` (`storage/backup/backup-cli/src/coordinators/restore.rs`) whenever `do_phase_1` runs with a `kv_snapshot` present, which is a normal/expected restore configuration (restoring ledger history plus latest state). Any operator who restores using a backup source they do not fully control (public bucket, third-party mirror, compromised storage credentials) is exposed. No special privilege is required from the storage-serving side beyond being able to serve chunk data for the `kv_snapshot` manifest — which is exactly the "authenticated API/proof-bearing response" trust boundary this bug class targets.

### Recommendation
Either (a) remove the unverified `KvOnly` fast path and always verify chunk proofs before writing to `state_kv_db`, or (b) after Phase 2's `TreeOnly` restore completes, add an explicit reconciliation pass that recomputes hashes of everything already persisted via `KvOnly` in Phase 1.a against the verified JMT leaves at the corresponding version, rejecting/overwriting any mismatched key. At minimum, `StateValueRestore::add_chunk` in `KvOnly` mode should require and check a proof (or the hash of each value) against a trusted root before persisting, closing the gap between what is written to `state_kv_db` and what the Merkle proof actually attests to.

### Proof of Concept
1. Stand up a backup restore using `RestoreCoordinator` with a `kv_snapshot` at version `V1` and a `tree_snapshot` at version `V2 > V1`, both hosted on a storage backend the "attacker" controls (e.g., a custom `BackupStorage` implementation).
2. For a state key `K` with legitimate value `Vgood` at `V1` that is never touched by any write set in transactions `V1+1..=V2`, have the malicious storage serve chunk data for the Phase-1.a `kv_snapshot` manifest with `(K, Vbad)` instead of `(K, Vgood)`. Because `add_chunk` in `KvOnly` mode never validates the proof (`storage/aptosdb/src/state_restore/mod.rs:215-218`), this substitution succeeds.
3. Phase 1.b replays the genuine transactions `V1+1..V2` (downloaded and verified normally), none of which touch `K`, so `state_kv_db` still holds `Vbad` for `K` afterward.
4. Phase 2.a restores the tree at `V2` in `TreeOnly` mode using the (genuine, unmodified) `tree_snapshot` manifest, which contains `(K, Vgood)`; verification succeeds because `TreeOnly` never touches `state_kv_db`, only building JMT nodes from `v.hash()` (`storage/aptosdb/src/state_restore/mod.rs:219-226`).
5. After restore, `db.get_root_hash(V2)` matches the ledger-info-verified root (containing `hash(Vgood)`), yet `state_kv_db.get_state_value(K, V2)` returns `Vbad`. Any subsequent transaction execution reading `K` operates on `Vbad`, diverging from the correct ledger state while all proof/root checks reported success.

### Citations

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

**File:** storage/backup/backup-cli/src/coordinators/restore.rs (L236-260)
```rust
        if do_phase_1 {
            info!(
                "Start restoring DB from version {} to tree snapshot version {}",
                txn_start_version, tree_snapshot.version,
            );

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

**File:** storage/backup/backup-cli/src/coordinators/restore.rs (L262-303)
```rust
            // phase 1.b: save the txn between the first txn of the first chunk and the tree snapshot
            let txn_manifests = transaction_backups
                .iter()
                .filter(|e| {
                    e.first_version <= tree_snapshot.version && e.last_version >= db_next_version
                })
                .map(|e| e.manifest.clone())
                .collect();
            assert!(
                db_next_version == 0
                    || transaction_backups.first().map_or(0, |t| t.first_version)
                        <= db_next_version,
                "Inconsistent state: first txn version {} is larger than db_next_version {}",
                transaction_backups.first().map_or(0, |t| t.first_version),
                db_next_version
            );
            // update the kv to the kv db
            // reset the global
            let mut transaction_restore_opt = self.global_opt.clone();
            // We should replay kv to include the version of tree snapshot so that we can get correct storage usage at that version
            // while restore tree only snapshots
            let kv_replay_version = if let Some(kv_snapshot) = kv_snapshot.as_ref() {
                kv_snapshot.version + 1
            } else {
                db_next_version
            };
            transaction_restore_opt.target_version = tree_snapshot.version;
            TransactionRestoreBatchController::new(
                transaction_restore_opt,
                Arc::clone(&self.storage),
                txn_manifests,
                Some(db_next_version),
                Some((kv_replay_version, true /* only replay KV */)),
                epoch_history.clone(),
                VerifyExecutionMode::NoVerify,
                None,
            )
            .run()
            .await?;
            // update the expected version for the first phase restore
            db_next_version = tree_snapshot.version;
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

**File:** storage/backup/backup-cli/src/backup_types/state_snapshot/restore.rs (L123-139)
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
        if let Some(epoch_history) = self.epoch_history.as_ref() {
            epoch_history.verify_ledger_info(&li)?;
        }
```
