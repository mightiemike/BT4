## Finding

### Title
`KvOnly` state-snapshot restore mode writes raw state values to durable storage without any Merkle-proof verification, allowing an untrusted backup source to corrupt committed ledger state - (File: `storage/aptosdb/src/state_restore/mod.rs`)

### Summary
Aptos's backup/restore subsystem is explicitly designed so that state-snapshot chunks fetched from a `BackupStorage` (which can be an arbitrary, possibly untrusted, cloud store) are validated against a cryptographically-committed root hash before being written into the local database. The `StateSnapshotReceiver::add_chunk` implementation in `StateSnapshotRestore<K, V>` however skips this validation entirely when `restore_mode == StateSnapshotRestoreMode::KvOnly`, a mode that is used in production restore flows (`RestoreCoordinator::run_impl`, phase 1.a) whenever a partial/pruned restore with `ledger_history_start_version` is requested. In that mode, arbitrary key/value pairs supplied by the backup source are written straight into `state_kv_db` with no cross-check against the accompanying `SparseMerkleRangeProof` or the manifest's authenticated root hash.

### Finding Description
In `storage/aptosdb/src/state_restore/mod.rs`: [1](#0-0) 

The `add_chunk` match arm for `KvOnly` is:
```rust
StateSnapshotRestoreMode::KvOnly => {
    let _timer = OTHER_TIMERS_SECONDS.timer_with(&["state_value_add_chunk"]);
    self.kv_restore.lock().as_mut().unwrap().add_chunk(chunk)?;
},
```
The `proof: SparseMerkleRangeProof` parameter of `add_chunk` is completely unused in this branch. Contrast this with `Default` mode, where the code explicitly calls `tree_restore.verify_chunk(...)` (which internally calls `self.verify(proof)` against the expected root hash, see `storage/jellyfish-merkle/src/restore/mod.rs:351-405`) *before* writing to `kv_restore`. `TreeOnly` mode also verifies via `add_chunk_impl`, which calls `verify_chunk` then `commit_chunk`.

`StateValueRestore::add_chunk` (lines 73-112 of the same file) itself performs no hashing or proof check either — it only tracks resume progress (`StateSnapshotProgress`) and blindly persists the `(key, version) -> value` pairs via `write_kv_batch`.

The higher-level caller, `StateSnapshotRestoreController::run_impl` in `storage/backup/backup-cli/src/backup_types/state_snapshot/restore.rs`, only validates the manifest-level root hash once, against the ledger-info-anchored transaction info: [2](#0-1) 
This check establishes that `manifest.root_hash` is the *authentic* state root at `manifest.version`, but it does **not** establish that any specific downloaded chunk's raw key/value bytes correspond to that root — that binding is only made by the per-chunk `proof.verify()` inside `verify_chunk`, which `KvOnly` never calls.

`RestoreCoordinator::run_impl` uses `StateSnapshotRestoreMode::KvOnly` in a real production restore path (phase 1.a, when restoring the KV data older than `ledger_history_start_version`): [3](#0-2) 

Because the `chunks` for this manifest are downloaded from `self.storage: Arc<dyn BackupStorage>` (an operator-supplied, potentially third-party/community-hosted backup source), a backup provider (or a man-in-the-middle on the storage channel) can serve a valid `manifest.json` (with the real, verifiable `root_hash`/proof file) while substituting the raw per-chunk key/value payloads with arbitrary/corrupted data. Since `KvOnly` mode never checks these raw values against the sparse-merkle range proof, this corrupted data is committed verbatim into the target node's `state_kv_db`.

### Impact Explanation
This breaks the core proof/storage invariant: "state values written to `state_kv_db` must be provably consistent with the Merkle root that is anchored to a signed `LedgerInfo`." Corrupted historical state written this way:
- Persists in the node's durable ledger data (`state_kv_db`) with no detection at write time, and no re-verification is performed afterward.
- Is used to compute `StateStorageUsage` progress that feeds the rest of the restore/bootstrap pipeline (comment: "restore the KV snapshot before ledger history start version, which also restore StateStorageUsage at the version"), and can be served by state-value read APIs for that version range.
- Represents committed state that differs from the correct historical VM result — exactly the class of impact called out as in-scope ("Committed state that differs from the correct VM result or corrupts durable ledger data").

Because this affects nodes bootstrapping/restoring against untrusted or compromised backup storage (a supported, documented configuration via `--ledger-history-start-version`), and no other layer in the restore pipeline re-validates this specific KV range against the Merkle tree, the practical effect is silent, permanent corruption of a node's historical ledger data store.

### Likelihood Explanation
Likelihood is moderate to high in specific deployment scenarios: any operator restoring a fullnode/validator from a public, shared, or third-party-hosted backup archive using the pruned-history restore option (`ledger_history_start_version`) is exposed. No special privilege is needed by the attacker beyond controlling (or intercepting) the backup storage's raw snapshot-chunk files while a valid overall manifest/proof-of-root exists (which is a much narrower and more feasible attack than forging the full ledger-info-anchored proof).

### Recommendation
In `storage/aptosdb/src/state_restore/mod.rs`, make `KvOnly` mode verify the same per-chunk `SparseMerkleRangeProof` against a caller-supplied expected root before writing values (e.g., by running the same hash/proof verification logic used in `Default`/`TreeOnly` mode, without necessarily persisting the tree nodes), or otherwise refuse to accept `KvOnly` restores from unauthenticated/untrusted sources. At minimum, document and enforce that `KvOnly` restore is only safe when chunk integrity is independently guaranteed (e.g., always paired with a same-session `TreeOnly`/`Default` verification of the identical byte stream), and add an explicit code-level check preventing silent divergence between `state_kv_db` contents and the JMT-committed root for the affected version range.

### Proof of Concept
1. Set up `RestoreCoordinator` with `ledger_history_start_version` > 0, pointing `BackupStorage` at an attacker-controlled or intercepted storage backend.
2. Serve the real `manifest.json` and its accompanying `proof` file (so the top-level `txn_info_with_proof.verify(...)` and `state_root_hash == manifest.root_hash` checks in `restore.rs:125-136` pass).
3. For the chunk files referenced by `manifest.chunks`, serve raw `(key, value)` payloads with modified/incorrect values (leaving the corresponding `SparseMerkleRangeProof` structurally valid for parsing, since it is never checked against these bytes).
4. Run the restore; observe that `StateSnapshotRestore::add_chunk` (KvOnly arm) writes the tampered values into `state_kv_db` via `StateValueRestore::add_chunk` → `write_kv_batch`, with no error raised, because `proof` is never invoked in this code path.
5. Query the resulting node for state at a version in the tampered range; the corrupted value is returned as if authentic.

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
