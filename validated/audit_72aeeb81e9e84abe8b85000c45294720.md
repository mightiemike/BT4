### Title
Unverified state-value proof in `KvOnly` restore mode allows corrupted state values to be written to durable storage - (File: `storage/aptosdb/src/state_restore/mod.rs`)

### Summary
`StateSnapshotRestore::add_chunk` has three restore modes. In `StateSnapshotRestoreMode::Default`, the incoming chunk is proof-verified against the expected Jellyfish Merkle root (`tree_restore.verify_chunk`) *before* the raw key/value pairs are written to the authoritative `state_kv_db`. In `StateSnapshotRestoreMode::KvOnly`, however, the code writes the raw chunk straight to `kv_restore` and never calls any verification against the accompanying `SparseMerkleRangeProof` — the proof argument is accepted but silently discarded.

### Finding Description [1](#0-0) 

```
fn add_chunk(&mut self, chunk: Vec<(K, V)>, proof: SparseMerkleRangeProof) -> Result<()> {
    match self.restore_mode {
        StateSnapshotRestoreMode::KvOnly => {
            self.kv_restore.lock().as_mut().unwrap().add_chunk(chunk)?;   // proof unused
        },
        StateSnapshotRestoreMode::TreeOnly => {
            self.tree_restore...add_chunk_impl(chunk.iter().map(|(k,v)| (k, v.hash())).collect(), proof)?;
        },
        StateSnapshotRestoreMode::Default => {
            // verify_chunk -> add_chunk -> commit_chunk
            self.tree_restore...verify_chunk(...)?;
            self.kv_restore...add_chunk(chunk)?;
            self.tree_restore...commit_chunk()?;
        },
    }
    Ok(())
}
```

Only `Default` mode calls `verify_chunk`, which authenticates the chunk's key/value hashes against `expected_root_hash` using the `SparseMerkleRangeProof`. `KvOnly` mode commits the raw values directly to `state_kv_db` via `StateValueWriter::write_kv_batch` with zero cryptographic authentication of the data.

This `KvOnly` mode is exercised in the backup/restore coordinator during the two-phase state-snapshot restore, where a KV-only snapshot is restored first (bridging a gap before the tree is available), and a separate `TreeOnly` restore independently checks the JMT structure using only value *hashes* (not the concrete blob content) supplied by the backup archive. Because these two phases are decoupled and only the `TreeOnly` path checks a Merkle proof (and even then only against hashes computed from whatever key/value pairs are handed to it — which may come from an entirely different, equally untrusted, chunk stream), the actual persisted values written through the `KvOnly` path are never bound to any accumulator/JMT-authenticated root. A backup storage provider (an explicitly untrusted party in this system — that's the entire reason `SparseMerkleRangeProof`/`TransactionInfoWithProof` verification exists in this exact file for the `Default` mode) can supply a manifest whose top-level `root_hash` is legitimately proof-verified via `TransactionInfoWithProof::verify` in `backup_types/state_snapshot/restore.rs`, `(state_root_hash == manifest.root_hash checked at lines 127-136)`, while feeding arbitrary/corrupted key-value blobs for chunks restored via `KvOnly`, since no per-chunk proof binds those exact bytes to that root.

### Impact Explanation
If exploited, an untrusted or compromised backup store can cause a restoring node to persist state values that were never actually committed on-chain, corrupting the authoritative `state_kv_db` used to answer authenticated state queries and to seed subsequent JMT roots for the same version. This is exactly the class of "committed state that differs from the correct VM result / corrupts durable ledger data" and "authenticated API output bound to the wrong ... object" that the gate calls out, since API responses fetching state values by version would return attacker-controlled data while the surrounding proof-verification illusion (manifest-level root hash check) suggests full authentication occurred.

### Likelihood Explanation
This requires control over, or compromise of, the backup archive/storage the restoring node fetches from, and only affects the `KvOnly` restore code path used during a specific coordinator restore phase. It is a real, self-contained code omission (verification skipped for one of three enumerated modes) rather than a purely theoretical concern, but it is contingent on the operator restoring from an untrusted/compromised backup source, and I was not able to fully trace whether a later stage (e.g. transaction replay against the restored range, or a final usage/consistency check) independently re-validates the KV content bit-for-bit against the tree before the node is considered "restored" and made to serve traffic — my last exploration into `storage/backup/backup-cli/src/coordinators/restore.rs` and `utils/mod.rs` to confirm this end-to-end was cut off before completion.

### Recommendation
Either (a) require and verify a proof for `KvOnly` mode too — validate each key/value's hash against `SparseMerkleRangeProof`/the corresponding leaf entries recoverable from the tree snapshot before writing to `state_kv_db`, or (b) if `KvOnly` mode is only ever meant to be used together with a subsequent full re-verification pass (e.g., recomputing/re-checking the JMT over the exact same K/V bytes written), make that invariant explicit and enforced in code (e.g., an assertion or a follow-up verification step that reads back the committed KV data and confirms hashes against the already-verified tree), rather than relying on operational assumptions.

### Proof of Concept
Not fully constructable without completing the trace through `coordinators/restore.rs`/`utils/mod.rs` (cut off due to tool-call limits). Conceptually: craft a `StateSnapshotBackup` manifest whose `manifest.root_hash` matches a legitimately-signed `LedgerInfo`/`TransactionInfoWithProof` (satisfying the check at `backup_types/state_snapshot/restore.rs:127-136`), but populate the KV-chunk blobs fed to a `KvOnly`-mode receiver with different key/value content than what produces that root; confirm they are accepted and persisted without any `verify_chunk`/proof check being invoked.

**Caveat**: Given the incomplete final verification of the full restore pipeline (whether a downstream step re-checks KV content against the tree), I cannot certify this as definitively exploitable end-to-end at High/Critical severity with full confidence — this should be treated as a strong candidate requiring the final trace step before filing as confirmed.

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
