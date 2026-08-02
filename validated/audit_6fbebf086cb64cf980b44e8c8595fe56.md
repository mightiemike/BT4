# Title
KV-only state snapshot restore silently discards the Sparse Merkle range proof, allowing corrupted historical state values from backup storage to be committed and served as authentic - (File: storage/aptosdb/src/state_restore/mod.rs)

### Summary
`StateSnapshotRestore::add_chunk` branches on `restore_mode`. In `StateSnapshotRestoreMode::KvOnly`, the function writes the raw `(key, value)` chunk straight into the state-KV store and never calls `verify_chunk`/`add_chunk_impl` on the tree side, meaning the `SparseMerkleRangeProof` argument that accompanies the chunk is completely unused for that mode. [1](#0-0) 

This mode is actually used in production restore flows (not just tests): `backup-cli`'s restore coordinator uses `StateSnapshotRestoreMode::KvOnly` for "phase 1.a" of a full DB restore, to populate historical state-KV data below the full tree snapshot version. [2](#0-1) 

The chunk's blobs and its accompanying proof are both loaded directly from the (untrusted, attacker/operator-selected) backup storage backend, with no independent recomputation: [3](#0-2) 

### Finding Description
The `StateSnapshotRestoreController::run_impl` only cryptographically authenticates the *root hash* of the snapshot manifest (`manifest.root_hash`) against the target `LedgerInfo`, via `txn_info_with_proof.verify(...)` and the `state_root_hash == manifest.root_hash` check. [4](#0-3) 

Binding each individual chunk of key/value pairs to that authenticated root is supposed to happen via the `SparseMerkleRangeProof` passed into `add_chunk`. For `StateSnapshotRestoreMode::Default` and `TreeOnly`, this binding is indeed enforced: `tree_restore.verify_chunk(...)` recomputes the accumulated frontier and fails if it doesn't match `expected_root_hash`. [5](#0-4) 

But for `StateSnapshotRestoreMode::KvOnly`, only `kv_restore.add_chunk(chunk)` is invoked — the `proof` parameter is dropped entirely, so nothing checks that the supplied `(key, value)` pairs actually belong to the Merkle root that was authenticated against the `LedgerInfo`. Any chunk of arbitrary state key/value data supplied by the backup storage backend for this phase will be accepted and persisted verbatim into `state_kv_db`.

This is exercised in the real restore coordinator: when restoring a full DB, phase 1.a calls `StateSnapshotRestoreController` with `restore_mode: StateSnapshotRestoreMode::KvOnly` to populate the KV store for versions below the tree-snapshot version, then phase 2 later restores the tree with `TreeOnly` for the *tree snapshot version only*, using separately-fetched (also storage-supplied) blobs. Phase 2's tree verification is over an entirely different, later version/manifest, and never re-validates the KV data phase 1 already persisted. The historical values populated by phase 1 for the version range `[db_next_version, kv_snapshot.version)` therefore remain permanently unauthenticated in the restored database.

### Impact Explanation
A compromised, malicious, or spoofed backup storage endpoint (which is exactly the untrusted input surface the manifest/proof mechanism exists to defend against) can inject arbitrary corrupted `StateValue`s for historical versions during a KV-only restore phase. Because no proof check occurs, these values are committed to durable storage as if legitimate and are subsequently served without any red flag by state-read APIs (e.g., `get_state_value_by_version`) bound to a specific, "authenticated" version — directly matching the in-scope impact "Authenticated API or state-view output bound to the wrong version, object, or proof context" and "Storage schemas, replay paths, and restore helpers must not reinterpret committed data into a different ledger state." Nodes bootstrapped this way silently diverge from the correct historical ledger state for the affected KV range.

### Likelihood Explanation
This requires an operator to run a DB restore against an untrusted or compromised backup source, which is a realistic scenario for validators/full nodes bootstrapping from public/community backup buckets. The bug is not a matter of "malicious peer/DoS" — it's a broken proof-binding invariant in code that explicitly accepts a `SparseMerkleRangeProof` parameter and silently ignores it in one of its three modes, so any caller of that mode (all current callers included) gets no verification despite the API surface implying otherwise.

### Recommendation
In `StateSnapshotRestore::add_chunk` for `StateSnapshotRestoreMode::KvOnly`, verify the incoming chunk against the expected root using the JMT range proof (in a memory-only mode that doesn't persist tree nodes) before writing to `kv_restore`, or refuse to construct a `KvOnly` receiver at all without a companion tree-verification pass covering the same key range/version, so no code path can persist state values that were never checked against the authenticated root hash.

### Proof of Concept
1. Operator/backup service publishes a `StateSnapshotBackup` manifest whose `root_hash`/`proof` correctly verify against a legitimate `LedgerInfo` (this part is real and checked).
2. For chunk files (`chunk.blobs`, `chunk.proof`) referenced by the manifest, the storage backend returns tampered blob data (arbitrary `(StateKey, StateValue)` pairs) while returning any syntactically valid `SparseMerkleRangeProof` (or even a stale/mismatched one).
3. `TransactionRestoreBatchController`/`StateSnapshotRestoreController` invoke restore with `StateSnapshotRestoreMode::KvOnly` (as done in `coordinators/restore.rs` phase 1.a).
4. `StateSnapshotRestore::add_chunk` calls only `kv_restore.add_chunk(chunk)`, never checking `proof` against `expected_root_hash` [1](#0-0) .
5. The tampered state values are committed to `state_kv_db` and are subsequently returned by historical state-value read APIs as if authentic, despite never having been cryptographically bound to the verified ledger root.

### Citations

**File:** storage/aptosdb/src/state_restore/mod.rs (L213-218)
```rust
    fn add_chunk(&mut self, chunk: Vec<(K, V)>, proof: SparseMerkleRangeProof) -> Result<()> {
        match self.restore_mode {
            StateSnapshotRestoreMode::KvOnly => {
                let _timer = OTHER_TIMERS_SECONDS.timer_with(&["state_value_add_chunk"]);
                self.kv_restore.lock().as_mut().unwrap().add_chunk(chunk)?;
            },
```

**File:** storage/aptosdb/src/state_restore/mod.rs (L227-249)
```rust
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

**File:** storage/backup/backup-cli/src/backup_types/state_snapshot/restore.rs (L190-215)
```rust
                tokio::spawn(async move {
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
