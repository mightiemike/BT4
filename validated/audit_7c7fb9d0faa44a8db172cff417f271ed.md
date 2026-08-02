### Title
Native-position state root recomputed at commit time can diverge from the executor's authoritative root, corrupting the `PositionStateWithSummary` Merkle root - (File: `storage/aptosdb/src/db/aptosdb_writer.rs`)

### Summary
Aptos-core includes a "native position" (trading-position) sub-ledger with its own JMT/SMT alongside the main account state. There are **two independent code paths** that can produce the `PositionStateWithSummary` (the position tree's checkpoint/root) for the same chunk of transactions:

1. **Execution-time path** (used when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is on): the root is computed inside the executor and carried through as `chunk.position_state_summary`.
2. **Storage-recompute path** (used when the flag is off): `AptosDB::position_summary_at_commit` independently reconstructs the same tree at commit time.

This is structurally identical to the Perennial bug pattern: two paths ("global" vs "local" processing in Perennial; "execution-computed" vs "storage-recomputed" here) are each supposed to reach the same next-valid state/checkpoint, but use different bases/boundaries to decide when to advance, so they can silently diverge and produce a wrong committed root.

### Finding Description
`AptosDB::commit_native_position` selects between the two paths: [1](#0-0) 

When `chunk.position_state_summary` is `None` (flag off), `position_summary_at_commit` is used: [2](#0-1) 

Two problems in this recompute path:

1. **Mismatched base vs. tip.** `extend_on_base` always freezes SMT updates against `persisted_base.summary()` — the disk-persisted snapshot, which the code itself documents as *lagging* the in-memory tip: [3](#0-2) 
   — while the actual tree data being extended (`latest`) is taken from `store.current_state()`, the more advanced pre-committed tip. `StateAndSummary::extend` performs `self.summary().global_state_summary.freeze(&base_summary.global_state_summary)`: [4](#0-3) 
   Each call to `extend_on_base` re-freezes against the *same* stale `persisted_base` even across multiple checkpoint boundaries processed within one chunk, rather than against the previous position-checkpoint actually produced inside this same computation. This is the same shape of bug as Perennial's `_processPositionGlobal`/`_processPositionLocal` disagreeing on which prior valid state to extend from.

2. **Checkpoint-boundary divergence.** The recompute path decides when to snapshot a position checkpoint using `checkpoint_within_chunk`, taken from the **main ledger's** checkpoint version, but only actually advances `last_checkpoint` if there happen to be pending native-position writes at that exact version: [5](#0-4) 
   If a chunk's ledger-checkpoint transaction carries no position writes, `last_checkpoint` is silently left at its old (stale) version instead of being re-stamped at the new checkpoint version, whereas the execution-time path stamps a checkpoint hash unconditionally at every checkpoint index via `get_state_checkpoint_hashes`, which always writes `Some(computed_last_checkpoint_hash)` at the checkpoint index regardless of whether the underlying state changed: [6](#0-5) 

Because these two computations are meant to be interchangeable (one is only used when the other is disabled) but use different bases and different checkpoint-advance conditions, they can produce different `PositionStateWithSummary` roots for the same underlying position writes, exactly mirroring the Perennial root cause: a version/timestamp mismatch between the "authoritative" and "locally recomputed" state-advance logic.

### Impact Explanation
The `PositionStateWithSummary` root feeds:
- The durable position Merkle DB commit (`PositionMerkleBatchCommitter` / `merklize_position`), i.e. committed ledger data.
- `position_state_checkpoint_hash()` on `TransactionInfo`, which is bound into the transaction-info accumulator and is used by restore/state-sync flows (`bootstrapper.rs::expected_snapshot_root`) as an authenticated proof root: [7](#0-6) 
- `DbReader::get_persisted_position_state_summary` / `get_pre_committed_position_state_summary`, which are authenticated state-view outputs consumed by execution as the "proof base" for subsequent chunks.

If the storage-side recompute diverges from the execution-computed root (e.g., due to toggling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, or a checkpoint boundary with no position writes followed later by writes flushed against a stale base), the durably committed position root would differ from the correct VM result. This corrupts durable ledger data and can cause a wrong Merkle root to be accepted as valid in subsequent proof verification and restore flows — squarely within the state-commitment/proof-integrity impact category.

### Likelihood Explanation
This requires the native-position subsystem to be enabled (`ENABLE_TRADING_NATIVE`) and reachable in a configuration where the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` flag differs between execution and storage expectations, or where a chunk boundary has a checkpoint transaction without position writes followed by later position writes — a condition that, like the invalid-oracle-version case in the source report, is plausible under normal operating conditions rather than requiring malicious behavior. I was not able to fully trace the exact semantics of `SparseMerkleTree::freeze`/`unfreeze` (in `aptos_scratchpad`) within the available budget to conclusively prove the numeric root divergence end-to-end; this is the main residual uncertainty in this finding, and would need to be confirmed with a targeted unit test that toggles the two code paths against the same chunk of transactions.

### Recommendation
- Eliminate the dual computation paths: always compute `PositionStateWithSummary` at execution time and pass it through to storage, removing `position_summary_at_commit` entirely, or
- If the storage-side recompute must be kept, make it deterministically match execution's checkpoint semantics: use the same base (the tip actually extended by preceding calls within the same commit, not a stale `persisted_base`) and stamp `last_checkpoint` unconditionally at every ledger-checkpoint version, not only when position writes are present at that exact version.
- Add a debug-assertion / consistency check (like `check_usage_consistency` for main state) that compares the recomputed root against the execution-supplied root whenever both are available, and fails fast on mismatch.

### Proof of Concept
A concrete PoC would need to construct two commit sequences with native-position enabled: (a) run with `COMPUTE_TRADING_NATIVE_STATE_ROOTS` on to get the execution-computed root for a chunk containing a ledger checkpoint transaction with no position writes followed by a later position write within the same chunk, and (b) run the same chunk with the flag off so `position_summary_at_commit` recomputes it, then compare `PositionStateWithSummary::root_hash()` from both paths. I was unable to execute this in the available tool budget (read-only code search only); this is flagged as unverified and should be confirmed with a unit test in `storage/aptosdb/src/state_store/tests/` mirroring the existing `hot_state_snapshot.rs` test harness, driving `commit_native_position` under both flag settings against identical write sets and asserting root equality.

### Citations

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L382-399)
```rust
        // Advance the position pipeline (merklize + persist + advance the base).
        // Flag on: the summary comes from execution on the chunk; off: compute
        // it here so the tree still tracks forward (not consensus-committed).
        if let Some(store) = bundle.state_store.as_ref() {
            let new_state = match chunk.position_state_summary {
                Some(summary) => summary.clone(),
                None => self.position_summary_at_commit(chunk)?,
            };
            let estimated_items = chunk.transaction_outputs.len();
            let mut bufstate = store.buffered_state_locked();
            bufstate.update(
                new_state,
                (),
                estimated_items,
                sync_commit || chunk.is_reconfig,
            )?;
        }
        Ok(())
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L406-465)
```rust
    fn position_summary_at_commit(
        &self,
        chunk: &ChunkToCommit,
    ) -> Result<PositionLedgerStateWithSummary> {
        let bundle = self
            .position
            .as_ref()
            .expect("called only when position is present");
        let store = bundle
            .state_store
            .as_ref()
            .expect("called only when state_store is present");
        let persisted_base = bundle
            .persisted
            .as_ref()
            .expect("persisted present when state_store is")
            .get();

        let (mut latest, mut last_checkpoint) = {
            let state = store.current_state();
            let current = state.lock();
            (current.latest().clone(), current.last_checkpoint().clone())
        };

        let chunk_first = chunk.first_version;
        let chunk_last_inclusive = chunk_first + chunk.transaction_outputs.len() as Version - 1;
        let checkpoint_within_chunk = chunk
            .state
            .last_checkpoint()
            .version()
            .filter(|v| (chunk_first..=chunk_last_inclusive).contains(v));

        let mut pending: HashMap<HashValue, PositionSlot> = HashMap::new();
        let extend_on_base = |latest: &PositionStateWithSummary,
                              version: Version,
                              updates: Vec<(HashValue, PositionSlot)>|
         -> Result<PositionStateWithSummary> {
            let proof_reader = PositionProofReader {
                merkle_db: bundle.merkle_db.clone(),
                version: persisted_base.version(),
            };
            latest.extend(version, updates, persisted_base.summary(), &proof_reader)
        };

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

**File:** storage/storage-interface/src/state_store/state_summary.rs (L412-419)
```rust
/// pre-committed position tip used to seed the in-memory parent when no
/// block parent is available. The async merkle committer can lag the tip,
/// so the two differ and must not be conflated.
pub struct ProvablePositionStateSummary<'db> {
    persisted: PositionStateWithSummary,
    pre_committed: LedgerWithSummary<PositionStateWithSummary>,
    db: &'db (dyn DbReader + Sync),
}
```

**File:** storage/storage-interface/src/state_store/sharded_jmt_state.rs (L144-168)
```rust
    pub fn extend(
        &self,
        new_version: Version,
        updates: Vec<(HashValue, Slot)>,
        base_summary: &StateSummary,
        proof_reader: &impl ProofRead,
    ) -> Result<Self> {
        let smt_updates: Vec<(HashValue, Option<HashValue>)> =
            updates.iter().map(|(k, s)| (*k, s.value_hash())).collect();
        let new_global = if smt_updates.is_empty() {
            self.summary().global_state_summary.clone()
        } else {
            self.summary()
                .global_state_summary
                .freeze(&base_summary.global_state_summary)
                .batch_update(smt_updates.iter(), proof_reader)
                .map_err(|e| {
                    AptosDbError::Other(format!("scratchpad SMT batch_update failed: {e:?}"))
                })?
                .unfreeze()
        };
        let new_summary = StateSummary::new_global_only(new_version, new_global);
        let new_state = self.state().extend(new_version, updates);
        Ok(Self::new(new_state, new_summary))
    }
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L192-233)
```rust
    fn get_state_checkpoint_hashes(
        execution_output: &ExecutionOutput,
        known_state_checkpoints: Option<Vec<Option<HashValue>>>,
        computed_last_checkpoint_hash: HashValue,
        label: &str,
    ) -> Result<Vec<Option<HashValue>>> {
        let _timer = OTHER_TIMERS.timer_with(&[&format!("get_{label}_checkpoint_hashes")]);

        let num_txns = execution_output.to_commit.len();
        let last_checkpoint_index = execution_output
            .to_commit
            .state_update_refs()
            .last_inner_checkpoint_index();

        if let Some(known) = known_state_checkpoints {
            ensure!(
                known.len() == num_txns,
                "Bad number of known {label} hashes. {} vs {}",
                known.len(),
                num_txns,
            );
            if let Some(idx) = last_checkpoint_index {
                ensure!(
                    known[idx] == Some(computed_last_checkpoint_hash),
                    "{label} root hash mismatch with known hashes passed in. {:?} vs {:?}",
                    known[idx],
                    Some(computed_last_checkpoint_hash),
                );
            }
            Ok(known)
        } else {
            if !execution_output.is_block {
                // We should enter this branch only in test.
                execution_output.to_commit.ensure_at_most_one_checkpoint()?;
            }

            let mut out = vec![None; num_txns];
            if let Some(index) = last_checkpoint_index {
                out[index] = Some(computed_last_checkpoint_hash);
            }
            Ok(out)
        }
```

**File:** state-sync/state-sync-driver/src/bootstrapper.rs (L1005-1008)
```rust
            StateKind::Position => target_transaction_info
                .position_state_checkpoint_hash()
                .ok_or_else(|| Error::UnexpectedError("Missing position state root!".into())),
        }
```
