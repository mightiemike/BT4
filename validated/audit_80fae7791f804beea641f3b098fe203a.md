## Finding [1](#0-0) 

### Title
Replay/state-sync verification (`ensure_match_transaction_info`) never validates `state_checkpoint_hash`, allowing a corrupted or diverged state root to pass as authentic — (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by the chunk executor's replay-verification path and by `db-tool`'s `replay_on_archive` to confirm that a freshly re-executed (or output-applied) transaction actually matches the `TransactionInfo` recorded on-chain/in a backup archive. It checks status, gas used, write-set hash, and event root hash, but explicitly skips checking `state_checkpoint_hash` (and `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`), which is the field that authenticates the state root at that version.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  compares locally-recomputed values against a `TransactionInfo`: status [2](#0-1) , gas used [3](#0-2) , write-set hash [4](#0-3) , and event root hash [5](#0-4) . It ends with a comment acknowledging the gap:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution.
``` [6](#0-5) 

This function is invoked directly in the chunk executor's replay verification loop, which is the path used to validate historical/archived transaction data during transaction replay (e.g. state-sync chunk replay and db restore/verify tooling): [7](#0-6) 

`TransactionInfo` is precisely the object stored in the transaction accumulator and is what proofs (`TransactionInfoWithProof`, `TransactionAccumulatorProof`) authenticate against a `LedgerInfo` root [8](#0-7) . Its `state_checkpoint_hash` field is meant to bind the accumulator leaf to the actual state root computed after the transaction/state-checkpoint. Because `ensure_match_transaction_info` omits validating this field, if a `TransactionInfo`'s `state_checkpoint_hash` (or, in V1, `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) does not match what local re-execution actually produces — whether due to storage corruption, a bug in state-checkpoint computation, or a maliciously altered backup/archive transaction info — the discrepancy is silently accepted as a "match" by replay/verification tooling.

### Impact Explanation
This breaks the intended state-proof/integrity invariant that "committed state must survive executor-to-storage handoff and replay unchanged," and that the transaction accumulator leaf must faithfully represent the actual state root. `db-tool`'s `replay_on_archive` and the chunk-executor's `verify_execution` (used during `VerifyExecutionMode::verify_all()` replay, e.g. in DB restore flows [9](#0-8) ) are the exact mechanisms operators and tooling rely on to detect state divergence between an archived/backed-up ledger and a freshly re-executed one. Since the state-checkpoint hash is never checked, a corrupted or hard-fork-diverged state root in historical data can pass verification undetected, defeating the purpose of replay-verification as a state-integrity safety net. This is scoped to state-commitment/proof-integrity per the task's gate (authenticated ledger data silently accepted despite diverging from correct VM/state result).

### Likelihood Explanation
The gap is deterministic and always present — every call to `ensure_match_transaction_info` (from both live chunk-executor replay and the standalone `db-tool replay_on_archive`) omits this check, so any state-checkpoint-hash mismatch (from any root cause: storage bug, corrupted backup data, non-deterministic state-checkpoint logic) is unconditionally missed by design, not merely by rare conditions.

### Recommendation
Extend `ensure_match_transaction_info` to compare the recomputed state checkpoint hash (and, for `TransactionInfoV1`, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` when applicable) against the corresponding fields in `txn_info`, failing verification (`ensure!`) on mismatch, consistent with how write-set and event-root hashes are already validated.

### Proof of Concept
Not directly exploitable as a PoC exploit chain (this is a missing-check integrity gap rather than an attacker-triggerable state corruption), but can be demonstrated by: constructing a `TransactionInfo` with a `state_checkpoint_hash` that does not match the actual state root produced by locally re-executing the corresponding transaction, then calling `ensure_match_transaction_info` (as done in `execution/executor/src/chunk_executor/mod.rs:692`) — the call returns `Ok(())` despite the state root mismatch, whereas an equivalent mismatch in write-set hash or event-root hash correctly returns an `Err`.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L685-706)
```rust
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

**File:** types/src/proof/definition.rs (L829-875)
```rust
/// `TransactionInfo` and a `TransactionAccumulatorProof` connecting it to the ledger root.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct TransactionInfoWithProof {
    /// The accumulator proof from ledger info root to leaf that authenticates the hash of the
    /// `TransactionInfo` object.
    pub ledger_info_to_transaction_info_proof: TransactionAccumulatorProof,

    /// The `TransactionInfo` object at the leaf of the accumulator.
    pub transaction_info: TransactionInfo,
}

impl TransactionInfoWithProof {
    /// Constructs a new `TransactionWithProof` object using given
    /// `ledger_info_to_transaction_info_proof`.
    pub fn new(
        ledger_info_to_transaction_info_proof: TransactionAccumulatorProof,
        transaction_info: TransactionInfo,
    ) -> Self {
        Self {
            ledger_info_to_transaction_info_proof,
            transaction_info,
        }
    }

    /// Returns the `ledger_info_to_transaction_info_proof` object in this proof.
    pub fn ledger_info_to_transaction_info_proof(&self) -> &TransactionAccumulatorProof {
        &self.ledger_info_to_transaction_info_proof
    }

    /// Returns the `transaction_info` object in this proof.
    pub fn transaction_info(&self) -> &TransactionInfo {
        &self.transaction_info
    }

    /// Verifies that the `TransactionInfo` exists in the ledger represented by the `LedgerInfo`
    /// at specified version.
    pub fn verify(&self, ledger_info: &LedgerInfo, transaction_version: Version) -> Result<()> {
        verify_transaction_info(
            ledger_info,
            transaction_version,
            &self.transaction_info,
            &self.ledger_info_to_transaction_info_proof,
        )?;
        Ok(())
    }
}
```

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L663-717)
```rust
    ) -> Result<()> {
        let (first_version, _) = self.replay_from_version.unwrap();
        restore_handler.reset_state_store();
        let replay_start = Instant::now();
        let db = DbReaderWriter::from_arc(Arc::clone(&restore_handler.aptosdb));
        let chunk_replayer = Arc::new(ChunkExecutor::<AptosVMBlockExecutor>::new(db));
        let ledger_update_stream = txns_to_execute_stream
            .try_chunks(BATCH_SIZE)
            .err_into::<anyhow::Error>()
            .map_ok(|chunk| {
                let (txns, persisted_aux_info, txn_infos, write_sets, events): (
                    Vec<_>,
                    Vec<_>,
                    Vec<_>,
                    Vec<_>,
                    Vec<_>,
                ) = chunk.into_iter().multiunzip();
                let chunk_replayer = chunk_replayer.clone();
                let verify_execution_mode = self.verify_execution_mode.clone();

                async move {
                    let _timer = OTHER_TIMERS_SECONDS.timer_with(&["enqueue_chunks"]);

                    tokio::task::spawn_blocking(move || {
                        chunk_replayer.enqueue_chunks(
                            txns,
                            persisted_aux_info,
                            txn_infos,
                            write_sets,
                            events,
                            &verify_execution_mode,
                        )
                    })
                    .await
                    .expect("spawn_blocking failed")
                }
            })
            .try_buffered_x(3, 1)
            .map_ok(|chunks_enqueued| {
                futures::stream::repeat_with(|| Result::Ok(())).take(chunks_enqueued)
            })
            .try_flatten();

        let db_commit_stream = ledger_update_stream
            .map_ok(|()| {
                let chunk_replayer = chunk_replayer.clone();
                async move {
                    let _timer = OTHER_TIMERS_SECONDS.timer_with(&["ledger_update"]);

                    tokio::task::spawn_blocking(move || chunk_replayer.update_ledger())
                        .await
                        .expect("spawn_blocking failed")
                }
            })
            .try_buffered_x(3, 1);
```
