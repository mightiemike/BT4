## Summary

Two closely related code paths in this repo skip verification of the state-checkpoint hash (the Merkle root of the state tree at a checkpoint boundary) when replaying or restoring transactions from a trusted-but-unverified source of write sets:

1. `TransactionOutput::ensure_match_transaction_info` explicitly omits checking `state_checkpoint_hash` / `hot_state_checkpoint_hash` against the computed output, as admitted by an inline TODO.
2. The `kv_replay` branch of `save_transactions_impl` (backup/state-sync restore helper) recomputes the state tree from write sets and commits it to disk without ever comparing the resulting root to the authenticated `TransactionInfo::state_checkpoint_hash()` that was cryptographically proven against the target `LedgerInfo`.

## Finding Description

`ensure_match_transaction_info` in [1](#0-0)  checks `status`, `gas_used`, `write_set` hash (`state_change_hash`), and `event_root_hash` against the trusted `TransactionInfo`, but explicitly does **not** check the state/hot-state checkpoint hashes, as documented by the comment: [2](#0-1) 

This function is the sole per-transaction integrity check used by:
- `ChunkExecutor::verify_execution` during backup/replay-verify of chunk execution [3](#0-2) 
- `db-tool`'s `replay_on_archive` tool [4](#0-3) 

Separately, the actual state commit for backup/state-sync restore in `save_transactions_impl` recomputes state directly from write sets via `calculate_state_and_put_updates` and persists it, with **no comparison at all** to the `txn_info.state_checkpoint_hash()` that was already fetched and cryptographically bound to the target `LedgerInfo` by the caller (e.g., transaction-chunk verification via accumulator range proofs): [5](#0-4) 

Contrast this with the normal block/chunk-execution commit path, `DoStateCheckpoint::get_state_checkpoint_hashes`, which does perform this comparison when `known_state_checkpoints` are supplied: [6](#0-5) 

That check is wired into `ChunkExecutor::update_ledger` for standard state-sync chunk application [7](#0-6) , but the `kv_replay` code path in `restore_utils.rs` bypasses `DoStateCheckpoint` entirely and writes state directly.

The accumulator/`TransactionInfo` proof machinery guarantees that the *write set* (via `state_change_hash`) delivered in a backup or chunk response is authentic (matches what was included in the proven `TransactionInfo`), but it does **not** independently prove the resulting state tree root. That binding is only established by re-deriving the state tree from write sets and comparing the result to `state_checkpoint_hash`. Since neither `ensure_match_transaction_info` nor the `kv_replay` commit path performs that comparison, any divergence between the locally recomputed state tree and the canonical, ledger-info-authenticated `state_checkpoint_hash` — e.g., from a state-store bug, an incomplete/stale base state, or an incorrectly applied write-set batch — will silently persist to durable storage and be reported as a successful, verified replay/restore.

## Impact Explanation

If the locally computed state root diverges from the authenticated `state_checkpoint_hash` during a KV-replay restore or during replay-verify tooling, the resulting node/database silently commits a *different* ledger state than the one certified by validator signatures, while:
- `LedgerCommitProgress` / `OverallCommitProgress` are still advanced [8](#0-7) , so the node believes it is fully synced/restored to the target version.
- Subsequent state reads, proofs, and API responses served from this node would be bound to the wrong (uncommitted-by-consensus) state values at a version that claims to be authenticated, since nothing flags the mismatch.
- Replay-verify tooling (`replay_on_archive`, debugger replay) that operators rely on to detect state divergence after upgrades/hard forks would report success even though the state has silently diverged — defeating its entire purpose as a corruption/hard-fork-divergence detector.

This is a genuine proof-binding gap in a restore/replay path that other integrity gates in the codebase (`DoStateCheckpoint`) demonstrate is normally enforced, but which is missing here.

## Likelihood Explanation

This does not require a malicious actor: any state-computation bug, storage-schema mismatch, or partial/incorrect base state supplied to `calculate_state_and_put_updates` during a KV-replay restore is sufficient to trigger silent divergence, because there is no cross-check against the proven checkpoint hash to catch it. The gap is explicitly acknowledged in-code (the TODO in `ensure_match_transaction_info`), indicating the authors are aware the check is currently missing for this class of hash, which increases confidence this is a real, not-yet-closed gap rather than a false positive.

I was not able to fully confirm within the available exploration whether every caller of `kv_replay=true` guarantees a fully-trusted, bug-free write-set/base-state pairing before calling `save_transactions_impl`, so the practical exploitability depends on there being any bug upstream that produces a write-set/state mismatch; this finding demonstrates the missing invariant rather than a full concrete trigger.

## Recommendation

- In `save_transactions_impl`'s `kv_replay` branch, after calling `calculate_state_and_put_updates`, compare the resulting root hash against `txn_infos[last].state_checkpoint_hash()` (and `hot_state_checkpoint_hash()` where applicable) and abort/return an error on mismatch, mirroring what `DoStateCheckpoint::get_state_checkpoint_hashes` already does for the normal execution path.
- Extend `TransactionOutput::ensure_match_transaction_info` (or its callers) to validate state/hot-state checkpoint hashes wherever a `TransactionOutput` corresponds to a checkpoint boundary, instead of leaving this as a known, documented gap.

## Proof of Concept

Not independently exploitable as a standalone PoC without reproducing an upstream bug in `calculate_state_and_put_updates` or supplying an inconsistent base state; the vulnerability is the *absence* of a specific invariant check (state-root-to-`state_checkpoint_hash` binding) in [5](#0-4)  and [1](#0-0) , demonstrated by direct code inspection and contrasted against the equivalent, present check in [6](#0-5) .

### Citations

**File:** types/src/transaction/mod.rs (L2139-2204)
```rust
    pub fn ensure_match_transaction_info(
        &self,
        version: Version,
        txn_info: &TransactionInfo,
        expected_write_set: Option<&WriteSet>,
        expected_events: Option<&[ContractEvent]>,
    ) -> Result<()> {
        const ERR_MSG: &str = "TransactionOutput does not match TransactionInfo";

        let expected_txn_status: TransactionStatus = txn_info.status().clone().into();
        ensure!(
            self.status() == &expected_txn_status,
            "{}: version:{}, status:{:?}, auxiliary data:{:?}, expected:{:?}",
            ERR_MSG,
            version,
            self.status(),
            self.auxiliary_data(),
            expected_txn_status,
        );

        ensure!(
            self.gas_used() == txn_info.gas_used(),
            "{}: version:{}, gas_used:{:?}, expected:{:?}",
            ERR_MSG,
            version,
            self.gas_used(),
            txn_info.gas_used(),
        );

        let write_set_hash = CryptoHash::hash(self.write_set());
        ensure!(
            write_set_hash == txn_info.state_change_hash(),
            "{}: version:{}, write_set_hash:{:?}, expected:{:?}, write_set: {:?}, expected(if known): {:?}",
            ERR_MSG,
            version,
            write_set_hash,
            txn_info.state_change_hash(),
            self.write_set,
            expected_write_set,
        );

        let event_hashes = self
            .events()
            .iter()
            .map(CryptoHash::hash)
            .collect::<Vec<_>>();
        let event_root_hash = InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash;
        ensure!(
            event_root_hash == txn_info.event_root_hash(),
            "{}: version:{}, event_root_hash:{:?}, expected:{:?}, events: {:?}, expected(if known): {:?}",
            ERR_MSG,
            version,
            event_root_hash,
            txn_info.event_root_hash(),
            self.events(),
            expected_events,
        );

        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
    }
```

**File:** execution/executor/src/chunk_executor/mod.rs (L373-413)
```rust
        let txn_infos = chunk_verifier.transaction_infos();
        let known_state_checkpoints = Some(
            txn_infos
                .iter()
                .map(|t| t.state_checkpoint_hash())
                .collect_vec(),
        );
        let known_hot_state_checkpoints =
            output.execution_output.hot_state_root_in_txn_info.then(|| {
                txn_infos
                    .iter()
                    .map(|t| t.hot_state_checkpoint_hash())
                    .collect_vec()
            });
        let compute_trading_native_state_roots =
            output.execution_output.compute_trading_native_state_roots;
        let known_position_state_checkpoints = compute_trading_native_state_roots.then(|| {
            txn_infos
                .iter()
                .map(|t| t.position_state_checkpoint_hash())
                .collect_vec()
        });
        let position_persisted = compute_trading_native_state_roots
            .then(|| ProvablePositionStateSummary::new_persisted(self.db.reader.as_ref()))
            .transpose()?;
        let state_checkpoint_output = DoStateCheckpoint::run()
            .execution_output(&output.execution_output)
            .parent_state_summary(&parent_state_summary)
            .persisted_state_summary(&ProvableStateSummary::new_persisted(
                self.db.reader.as_ref(),
            )?)
            .maybe_known_state_checkpoints(known_state_checkpoints)
            .maybe_known_hot_state_checkpoints(known_hot_state_checkpoints)
            // Parent position summary is chained across chunks by the commit
            // queue (seeded from the pre-committed position tip); the persisted
            // base supplies cold-key proofs. The known-hash check validates the
            // computed root against the committed TransactionInfos.
            .maybe_parent_position_state_summary(parent_position_state_summary.as_ref())
            .maybe_persisted_position_state_summary(position_persisted.as_ref())
            .maybe_known_position_state_checkpoints(known_position_state_checkpoints)
            .build()?;
```

**File:** execution/executor/src/chunk_executor/mod.rs (L684-706)
```rust
        // not `zip_eq`, deliberately
        for (version, txn_out, txn_info, write_set, events) in multizip((
            begin_version..end_version,
            &execution_output.to_commit.transaction_outputs,
            transaction_infos.iter(),
            write_sets.iter(),
            event_vecs.iter(),
        )) {
            if let Err(err) = txn_out.ensure_match_transaction_info(
                version,
                txn_info,
                Some(write_set),
                Some(events),
            ) {
                return if verify_execution_mode.is_lazy_quit() {
                    error!("(Not quitting right away.) {}", err);
                    verify_execution_mode.mark_seen_error();
                    Ok(version + 1)
                } else {
                    Err(err)
                };
            }
        }
```

**File:** storage/db-tool/src/replay_on_archive.rs (L388-405)
```rust
        for idx in 0..cur_txns.len() {
            let version = *current_version;
            *current_version += 1;

            if let Err(err) = executed_outputs[idx].ensure_match_transaction_info(
                version,
                &expected_txn_infos[idx],
                Some(&expected_writesets[idx]),
                Some(&expected_events[idx]),
            ) {
                cur_txns.drain(0..idx + 1);
                cur_persisted_aux_info.drain(0..idx + 1);
                expected_txn_infos.drain(0..idx + 1);
                expected_events.drain(0..idx + 1);
                expected_writesets.drain(0..idx + 1);

                return Ok(Some(err));
            }
```

**File:** storage/aptosdb/src/backup/restore_utils.rs (L267-275)
```rust
    if kv_replay && first_version > 0 && state_store.get_usage(Some(first_version - 1)).is_ok() {
        let (ledger_state, _hot_state_updates) = state_store.calculate_state_and_put_updates(
            &StateUpdateRefs::index_write_sets(first_version, write_sets, write_sets.len(), vec![]),
            &mut ledger_db_batch.ledger_metadata_db_batches, // used for storing the storage usage
            state_kv_batches,
        )?;
        // n.b. ideally this is set after the batches are committed
        state_store.set_state_ignoring_summary(ledger_state);
    }
```

**File:** storage/aptosdb/src/backup/restore_utils.rs (L277-289)
```rust
    let last_version = first_version + txns.len() as u64 - 1;
    ledger_db_batch
        .ledger_metadata_db_batches
        .put::<DbMetadataSchema>(
            &DbMetadataKey::LedgerCommitProgress,
            &DbMetadataValue::Version(last_version),
        )?;
    ledger_db_batch
        .ledger_metadata_db_batches
        .put::<DbMetadataSchema>(
            &DbMetadataKey::OverallCommitProgress,
            &DbMetadataValue::Version(last_version),
        )?;
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L206-220)
```rust
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
```
