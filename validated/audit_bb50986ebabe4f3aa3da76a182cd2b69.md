Found a concrete integrity bug in the native-position truncation path.

### Title
`delete_position_value_and_index` deletes position values at the wrong key, leaving stale value rows and orphaned index rows after truncation - (File: `storage/aptosdb/src/utils/truncation_helper.rs`)

### Summary
`PositionValueSchema` rows are keyed by `(state_key_hash, version)` where `version` is the version at which the value was written (see `native_state_committer.rs::apply`, which does `pos_batch.put::<PositionValueSchema>(&(state_key_hash, version), &maybe_value)`). The stale-index (`StalePositionValueIndex`) instead records `stale_since_version` (the version of the *newer* write that supersedes the row) and `version` (the version of the *actual value row* being made stale). When truncating the position DB, `delete_position_value_and_index` incorrectly reconstructs the value key from `(index.state_key_hash, index.stale_since_version)` instead of `(index.state_key_hash, index.version)`.

### Finding Description
In `truncation_helper.rs`:

```rust
fn delete_position_value_and_index(
    db_shard: &DB,
    start_version: Version,
    batch: &mut SchemaBatch,
) -> Result<()> {
    let mut iter = db_shard.iter::<StalePositionValueIndexSchema>()?;
    iter.seek(&start_version)?;
    for row in iter {
        let (index, _) = row?;
        batch.delete::<StalePositionValueIndexSchema>(&index)?;
        batch.delete::<PositionValueSchema>(&(index.state_key_hash, index.stale_since_version))?;
    }
    Ok(())
}
``` [1](#0-0) 

Compare this to how the index and value are actually produced in `NativeStateCommitter::apply`: the value row is written at `(state_key_hash, version)` (the version of *this* write), while the stale-index row is `StalePositionValueIndex { stale_since_version: version, version: prior_v, state_key_hash }` — i.e. `version` in the index refers to the *previous* write's version (the one becoming stale), not the current one. [2](#0-1) 

So when truncation walks stale-index rows with `stale_since_version >= start_version` and tries to delete the corresponding `PositionValueSchema` row, it deletes `(state_key_hash, stale_since_version)` — the value row that was just written by the truncated write — rather than `(state_key_hash, index.version)`, which is the actual now-obsolete prior value row that should be removed. This exact same bug is confirmed by the normal pruner (`state_kv_shard_pruner.rs`), which correctly deletes using `(index.state_key_hash, index.version)`:
```rust
batch.delete::<S::ValueSchema>(&(index.state_key_hash, index.version))?;
``` [3](#0-2) 

This confirms `truncation_helper.rs`'s use of `stale_since_version` instead of `version` is the injected divergence from the established, correct pattern used elsewhere in the same codebase for the identical index/value relationship.

The consequence mirrors the H-6 bug class exactly: truncation removes the *wrong* durable value row (the newly-committed one that should be kept as the current value for the target version) and never removes the *actually stale* row (which the index was supposed to reference), corrupting the committed KV state for `PositionValueSchema` after any crash-recovery truncation (`sync_position_commit_progress` → `truncate_position_db_shards` → `truncate_position_db_single_shard` → `delete_position_value_and_index`, invoked from `init_native_position`). [4](#0-3) 

### Impact Explanation
After any node restart that requires truncating the `position_db` back to `OverallCommitProgress` (a normal crash-recovery path, not attacker-triggered by itself, but a state-commit-integrity guarantee that must hold on every validator/full node restart), the committed durable position-value state diverges from the correct VM result: the value that should remain visible at the truncated version is deleted, while the actually-superseded (stale) value row is left behind un-pruned. Subsequent reads via `PositionDb::get_position_value` (used to resolve JMT leaves) can therefore either return `None` for a key that should have a value, or resolve to a leftover stale row instead of the truncated-to version's correct value — a genuine state-commitment corruption of durable ledger data, meeting the "Committed state that differs from the correct VM result or corrupts durable ledger data" bar.

### Likelihood Explanation
This code path executes on every restart path that requires truncation of `position_db` (i.e., whenever a crash happens between the position-DB commit and the ledger `OverallCommitProgress` write — a documented, expected recovery scenario per the comments in `sync_position_commit_progress`), so it is reachable without any attacker privilege, purely through ordinary node operation/crash-restart. Given native-position/"TradingNative" is gated behind `ENABLE_TRADING_NATIVE`, likelihood on general mainnet nodes depends on that feature being enabled, but wherever it is enabled the bug triggers deterministically on the documented restart-recovery flow.

### Recommendation
Fix `delete_position_value_and_index` to delete the value keyed by `index.version` (the actually-stale prior value), consistent with `state_kv_shard_pruner.rs`:
```rust
batch.delete::<PositionValueSchema>(&(index.state_key_hash, index.version))?;
```
Additionally audit whether the newly-written value row at `stale_since_version` also needs deletion when it falls at or after `start_version` (since it too is "ahead" of the truncation target and should not survive), which may require deleting both `index.version` and `index.stale_since_version` rows explicitly rather than conflating the two.

### Proof of Concept
1. Enable the native-position subsystem (`ENABLE_TRADING_NATIVE`).
2. Commit a `Position` write for `state_key_hash = H` at version 5 with value `V5` (no prior value ⇒ index `{stale_since_version:5, version:NO_PREV_VERSION, H}`), then commit an update to the same key at version 10 with value `V10` (index `{stale_since_version:10, version:5, H}`).
3. Crash/simulate a scenario where `position_db` commit progress is ahead of `OverallCommitProgress`, forcing `sync_position_commit_progress` to call `truncate_position_db_shards(position_db, 5)` (i.e. `start_version = 6`).
4. `delete_position_value_and_index` iterates stale-index rows with `stale_since_version >= 6`, finds the `{10, 5, H}` row, and deletes `PositionValueSchema[(H, 10)]` — but never touches `PositionValueSchema[(H,5)]` (the row referenced by `index.version = 5`, which is exactly the value that should still exist since we truncated to version 5). Meanwhile the actually-obsolete write is untouched (there is none in this simple example, but in a longer chain with multiple supersessions this leaves genuinely stale rows behind while destroying the live one).
5. `PositionDb::get_position_value(H, 5)` (used by the JMT leaf lookup) subsequently either returns nothing or a wrong/stale value for the position, corrupting the committed state view exposed to restore/replay and query paths. [5](#0-4)

### Citations

**File:** storage/aptosdb/src/utils/truncation_helper.rs (L171-187)
```rust
fn delete_position_value_and_index(
    db_shard: &DB,
    start_version: Version,
    batch: &mut SchemaBatch,
) -> Result<()> {
    // The stale-index is keyed by `stale_since_version` BE; forward
    // seek lands on the first row to delete. Every kv write has a
    // paired stale-index row (first writes use `NO_PREV_VERSION`).
    let mut iter = db_shard.iter::<StalePositionValueIndexSchema>()?;
    iter.seek(&start_version)?;
    for row in iter {
        let (index, _) = row?;
        batch.delete::<StalePositionValueIndexSchema>(&index)?;
        batch.delete::<PositionValueSchema>(&(index.state_key_hash, index.stale_since_version))?;
    }
    Ok(())
}
```

**File:** storage/aptosdb/src/native_state_committer.rs (L99-125)
```rust
            // In-chunk map first (same-chunk earlier writes), then DB.
            let prior_v = match in_chunk_prior.get(&state_key_hash) {
                Some(&v) => Some(v),
                None => self
                    .position_db
                    .find_prior_version(state_key_hash, version)
                    .map_err(|e| AptosDbError::Other(format!("find_prior_version: {e}")))?,
            };
            // Always emit a stale-index row — first writes use
            // `NO_PREV_VERSION` and the pruner skips them via
            // `is_first_write()`. Lets truncation iterate this CF
            // alone to reach every kv row.
            pos_batch
                .put::<StalePositionValueIndexSchema>(
                    &StalePositionValueIndex {
                        stale_since_version: version,
                        version: prior_v.unwrap_or(StalePositionValueIndex::NO_PREV_VERSION),
                        state_key_hash,
                    },
                    &(),
                )
                .map_err(|e| AptosDbError::Other(format!("stale_position_value_index put: {e}")))?;
            pos_batch
                .put::<PositionValueSchema>(&(state_key_hash, version), &maybe_value)
                .map_err(|e| {
                    AptosDbError::Other(format!("position_value batch put failed: {e}"))
                })?;
```

**File:** storage/aptosdb/src/pruner/state_kv_pruner/state_kv_shard_pruner.rs (L74-77)
```rust
            batch.delete::<S::StaleIndexSchema>(&index)?;
            if !index.is_first_write() {
                batch.delete::<S::ValueSchema>(&(index.state_key_hash, index.version))?;
            }
```

**File:** storage/aptosdb/src/db/aptosdb_native_position.rs (L189-207)
```rust
    fn sync_position_commit_progress(
        &self,
        position_db: &PositionDb,
        merkle_db: &PositionMerkleDb,
    ) -> Result<Option<Version>> {
        let v_overall = self.ledger_db.metadata_db().get_synced_version()?;

        if let Some(v_kv) = get_position_commit_progress(position_db)? {
            let target = v_overall.map_or(0, |v| std::cmp::min(v_kv, v));
            if v_kv != target {
                info!(
                    v_kv = v_kv,
                    v_overall = ?v_overall,
                    target = target,
                    "Truncating position_db down to chain's OverallCommitProgress."
                );
            }
            truncate_position_db_shards(position_db, target)?;
        }
```

**File:** storage/aptosdb/src/position_db.rs (L246-262)
```rust
    pub fn get_position_value(
        &self,
        state_key_hash: HashValue,
        version: Version,
    ) -> Result<Option<(Version, StateValue)>> {
        let mut read_opts = ReadOptions::default();
        read_opts.set_prefix_same_as_start(true);
        let shard = ShardedKvDb::shard_of_hash(state_key_hash);
        let mut iter = self
            .shard(shard)
            .iter_with_opts::<PositionValueSchema>(read_opts)?;
        iter.seek(&(state_key_hash, version))?;
        Ok(iter
            .next()
            .transpose()?
            .and_then(|((_, version), value_opt)| value_opt.map(|value| (version, value))))
    }
```
