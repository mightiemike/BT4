### Title
Skipped position-state checkpoint advance on empty pending writes corrupts the "last checkpoint" summary version - (File: `storage/aptosdb/src/db/aptosdb_writer.rs`)

### Summary
Aptos-core's native "trading Position" state (`aptos-move/framework/aptos-experimental/sources/trading/position`) maintains its own sharded Jellyfish-Merkle-style summary (`PositionStateWithSummary`), computed either at execution time (`execution/executor/src/workflow/do_state_checkpoint.rs::compute_position_checkpoint`) or, when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is off, recomputed at commit time in `storage/aptosdb/src/db/aptosdb_writer.rs::position_summary_at_commit`. The two implementations are supposed to be equivalent, deterministic re-derivations of the same commitment (analogous to the report's requirement that redemption bookkeeping stay consistent across code paths). They diverge on the empty-writes edge case at a checkpoint boundary.

### Finding Description
In `position_summary_at_commit` [1](#0-0) , the checkpoint advance is guarded by `!pending.is_empty()`:

```
if Some(version) == checkpoint_within_chunk && !pending.is_empty() {
    let updates: Vec<_> = std::mem::take(&mut pending).into_iter().collect();
    latest = extend_on_base(&latest, version, updates)?;
    last_checkpoint = latest.clone();
}
```

If the chunk's designated checkpoint version is reached while `pending` happens to be empty (i.e., no `Position` native writes occurred between the start of the chunk and the checkpoint transaction, inclusive), the block is skipped entirely — `last_checkpoint` is never reassigned and keeps whatever value it held from before the loop (`current.last_checkpoint().clone()`, i.e., the *previous* chunk's checkpoint, at the *previous* version).

By contrast, the execution-time implementation `compute_position_checkpoint` in `do_state_checkpoint.rs` always calls `extend` at the checkpoint version regardless of whether any position writes occurred: [2](#0-1) . This unconditionally advances the checkpoint summary's version, even with an empty update set.

The two code paths are meant to produce the same `PositionLedgerStateWithSummary` deterministically (per the comment in `commit_native_position`: "Flag on: the summary comes from execution on the chunk; off: compute it here so the tree still tracks forward"), but the commit-time fallback silently leaves `last_checkpoint` stale-versioned whenever a chunk's checkpoint boundary has no pending native-position writes. This is a version/root binding bug: the persisted "last_checkpoint" summary used as the base for the *next* chunk's extension (`bundle.persisted...get()` / `parent_last_checkpoint`) no longer corresponds to the actual checkpoint version of the committed ledger, silently corrupting the durable position-state commitment chain.

### Impact Explanation
The immediate effect is committed, durable position-state summary data (used as the base for subsequent extends, for merkle proof serving via `PositionProofReader`, and potentially exposed through position-state chunk/proof APIs such as `get_hot_state_value_chunk_proof`-style flows) that is bound to the wrong version. Any consumer that trusts `last_checkpoint.version()` to reflect the checkpoint transaction it is supposed to represent will operate on a stale/mismatched root, which can cascade into wrong proof roots for later restore/replay of the position tree, or incorrect base states for the next `extend` call, propagating the divergence forward in the durable position ledger.

### Likelihood Explanation
I was unable to fully verify severity because: (1) I could not confirm whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` off is ever exercised on mainnet vs. only in specific migration/backfill scenarios, (2) I could not inspect the `extend` implementation of `PositionStateWithSummary`/`LedgerWithSummary` to confirm whether extending with an empty update set is a true no-op besides the version bump (if it truly is a no-op except for version, the root hash itself might be unaffected and only the version metadata would be wrong — reducing this to a version-binding bug rather than a root-corruption bug), and (3) the "trading Position" feature is a bespoke extension (`aptos-experimental`/`position-natives`) whose consensus criticality relative to the main state root I could not fully establish from available code — the commit comment explicitly states this fallback path result is "not consensus-committed," which may place it outside the "state-commitment gate" if this native-position tree is not part of the validated ledger state root. Given these unresolved uncertainties around mainnet exposure and consensus criticality, I cannot confirm this rises to high/critical impact with full confidence.

### Recommendation
In `position_summary_at_commit`, remove the `!pending.is_empty()` guard so the checkpoint advance (`extend_on_base` + `last_checkpoint = latest.clone()`) always executes when `Some(version) == checkpoint_within_chunk`, matching the unconditional-extend semantics of `compute_position_checkpoint` in `do_state_checkpoint.rs`. Add a regression test asserting that both code paths (execution-time and commit-time fallback) produce an identical `PositionLedgerStateWithSummary` (same version and root) for a chunk whose checkpoint boundary has zero pending native-position writes.

### Proof of Concept
Could not be fully constructed without running the storage/executor test harness. Conceptually:
1. Enable native position state but set config so `compute_trading_native_state_roots` is false (execution-time path skipped) and rely on `commit_native_position`'s fallback.
2. Construct a chunk where the checkpoint transaction (and all transactions before it in the chunk) perform no `Position` native writes, but a transaction after the checkpoint within the same chunk does write to a `Position` key.
3. Observe that after commit, `last_checkpoint` (queryable via `bundle.persisted`/`PositionLedgerStateWithSummary`) retains the version/root from the *previous* chunk's checkpoint rather than the current chunk's checkpoint version, while `latest` advances correctly past it — producing a `last_checkpoint` inconsistent with the actual checkpoint transaction version, which the parallel execution-time implementation in `do_state_checkpoint.rs` would not exhibit (`test_helper.rs`/`restore_test.rs` harnesses referenced in the search, e.g. [3](#0-2) , illustrate the style of test that would need to be extended to cover this divergence).

### Citations

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L450-465)
```rust
        for (i, output) in chunk.transaction_outputs.iter().enumerate() {
            let version = chunk_first + i as Version;
            for (key, op) in output.write_set().native_position_iter() {
                let value_hash = op.as_write_op().as_state_value_opt().map(CryptoHash::hash);
                pending.insert(key.hash(), PositionSlot {
                    state_key: key.clone(),
                    value_hash,
                    value: None,
                });
            }
            if Some(version) == checkpoint_within_chunk && !pending.is_empty() {
                let updates: Vec<_> = std::mem::take(&mut pending).into_iter().collect();
                latest = extend_on_base(&latest, version, updates)?;
                last_checkpoint = latest.clone();
            }
        }
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L147-166)
```rust
        let (new_latest, new_last_checkpoint) = if let Some(ci) = last_checkpoint_index {
            let checkpoint_version = first_version + ci as u64;
            let new_ckpt = parent_latest.extend(
                checkpoint_version,
                collect(0..ci + 1),
                base_summary,
                persisted,
            )?;
            if ci + 1 == num_txns {
                (new_ckpt.clone(), new_ckpt)
            } else {
                let last_version = first_version + num_txns as u64 - 1;
                let new_latest = new_ckpt.extend(
                    last_version,
                    collect(ci + 1..num_txns),
                    base_summary,
                    persisted,
                )?;
                (new_latest, new_ckpt)
            }
```

**File:** storage/aptosdb/src/state_restore/restore_test.rs (L151-167)
```rust
prop_compose! {
    fn arb_btree_map(min_quantity: usize)(tree in btree_map(any::<ValueBlob>(), any::<ValueBlob>(), min_quantity..1000)) -> BTreeMap<HashValue, (ValueBlob, ValueBlob)> {
        tree.into_iter().map(|(k, v)| (CryptoHash::hash(&k), (k, v))).collect()
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(10))]

    #[test]
    fn test_restore_without_interruption(
        btree in arb_btree_map(1),
        target_version in 0u64..2000,
    ) {
        let restore_db = Arc::new(MockSnapshotStore::default());
        // For this test, restore everything without interruption.
        restore_without_interruption(&btree, target_version, &restore_db, true);
```
