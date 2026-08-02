## Finding

### Title
`TransactionOutput::ensure_match_transaction_info` never validates the state-checkpoint (SMT) root hash, so replay-verify tooling can silently accept a diverged world state - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole comparator used by the chunk-replayer/replay-verify code paths (`db-tool replay_on_archive`, `db-tool replay_verify`, `backup-cli` restore/verify, `aptos-debugger`, `cli` past-transaction execution) to confirm that locally re-executed transactions match the authenticated `TransactionInfo` recorded on-chain/in a backup. This comparator checks status, gas used, the write-set hash (`state_change_hash`) and the event root hash, but it never checks `TransactionInfo::state_checkpoint_hash()` — the periodic Sparse-Merkle-Tree root that actually commits to the full world state. A local re-execution that produces the *same write set and events* but a *different resulting global state tree* (e.g. due to a JMT/state-merge bug, a corrupted restore, or tampered/malicious archive data) will pass `ensure_match_transaction_info` even though the authenticated state root diverges.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` in [1](#0-0)  only asserts:
- `status()` matches the status derived from `txn_info.status()`
- `gas_used()` matches `txn_info.gas_used()`
- `CryptoHash::hash(self.write_set())` matches `txn_info.state_change_hash()`
- the event-accumulator root matches `txn_info.event_root_hash()`

It never reads or compares `txn_info.state_checkpoint_hash()` (nor `hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()` on the V1 variant). The code itself documents this gap for the position/hot-state fields via a TODO comment right above the `Ok(())`: [2](#0-1) 

but the *primary* `state_checkpoint_hash` (the field storing "the root hash of the Sparse Merkle Tree describing the world state at the end of this transaction", see [3](#0-2) ) is likewise absent from the comparison — structurally, `TransactionOutput` doesn't even carry a state-root value to compare against, since state checkpointing happens outside of `TransactionOutput` (in `DoStateCheckpoint`/ledger update), so nothing in this function can catch it.

This comparator is the verification gate for:
- `ChunkExecutorInner::verify_execution`, used by `TransactionReplayer::enqueue_chunks` [4](#0-3) 
- `db-tool`'s `replay_on_archive` [5](#0-4) 
- `backup-cli`'s transaction restore/verify flow (`go_through_verified_chunks` / `replay_transactions`), which is the mechanism operators use to validate a backup or an archive against the historically authenticated `TransactionInfo`s before trusting/committing the replayed data [6](#0-5) 

None of these call sites independently re-derive and compare the state checkpoint hash; they all rely on `ensure_match_transaction_info` as the pass/fail signal for "does my local re-execution match the authenticated ledger."

By contrast, note that the *online* state-sync chunk-executor path (`StateSyncChunkVerifier::verify_chunk_result`) uses a different, stronger check — `ensure_transaction_infos_match`, which compares whole `TransactionInfo` values (including `state_checkpoint_hash`) — see [7](#0-6) . This shows the codebase already has, and depends on, a stronger invariant elsewhere, confirming that `ensure_match_transaction_info`'s weaker check is a genuine gap rather than an intentional design choice for this class of validation.

### Impact Explanation
Replay-verify and archive/backup verification exist specifically to catch state-commitment divergence — i.e., a locally re-executed ledger state that differs from the authenticated on-chain root (due to a determinism bug, database corruption, or a tampered backup/archive). Because `ensure_match_transaction_info` never checks `state_checkpoint_hash` (the actual SMT root that authenticates the full account state), a divergence in the committed world state that doesn't happen to also change the write-set hash of the *last* transaction in a checkpoint window (or that stems from anything upstream of the write set, e.g. how prior state was merged/restored) will not be detected by this gate. Operators relying on `replay_on_archive`/backup verify to confirm ledger integrity before trusting/restoring data can be given a false "verified" result while the actual persisted state tree is corrupted or diverged from the authenticated ledger — a hard-fork-class integrity failure that this exact tooling is meant to catch.

### Likelihood Explanation
Low-to-moderate: this is a secondary/defense-in-depth check, not the primary consensus safety mechanism (block execution/StateSync's `ensure_transaction_infos_match` still enforces the full comparison for live consensus and state-sync ledger commits). Exploiting this gap in a way that has real consequences requires either an actual latent state-computation bug elsewhere in the stack, or a malicious/corrupted backup/archive data source. However, its entire *purpose* is to catch exactly those scenarios, so the missing check materially weakens Aptos's tooling for detecting state divergence in restore/replay contexts.

### Recommendation
Extend `TransactionOutput::ensure_match_transaction_info` (or add a companion check invoked from the same call sites) to compare the locally computed state-checkpoint hash (and hot-state/position-state checkpoint hashes when applicable) against `txn_info.state_checkpoint_hash()` whenever a checkpoint is expected at that version, mirroring the stronger comparison already performed by `ensure_transaction_infos_match` in the state-sync chunk verifier. At minimum, resolve the existing TODO for the trading-native/position-state hashes and additionally add the missing `state_checkpoint_hash` comparison, since it is not gated behind any feature flag and applies to the base protocol today.

### Proof of Concept
Not directly exploitable as a standalone PoC without an actual state-divergence bug or a tampered backup file; the code-level proof is structural: 
1. Construct a `TransactionOutput` whose write set and events replay identically to a target `TransactionInfo` (same `write_set`/`events`), so `state_change_hash` and `event_root_hash` match. 
2. Ensure the underlying JMT/global state that this write set is applied on top of differs from the authenticated chain's pre-state (e.g., via a corrupted restore of a prior chunk). 
3. Call `TransactionReplayer::enqueue_chunks` → `verify_execution` (or `db-tool replay_on_archive`) with this transaction/`TransactionInfo` pair — `ensure_match_transaction_info` at [1](#0-0)  returns `Ok(())` despite the resulting Sparse Merkle Tree root diverging from `txn_info.state_checkpoint_hash()`, since that field is never read in the comparator.

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

**File:** types/src/transaction/mod.rs (L2405-2412)
```rust
    /// The hash value summarizing all changes caused to the world state by this transaction.
    /// i.e. hash of the output write set.
    state_change_hash: HashValue,

    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,
```

**File:** execution/executor/src/chunk_executor/mod.rs (L648-707)
```rust
    fn verify_execution(
        &self,
        transactions: &[Transaction],
        persisted_aux_info: &[PersistedAuxiliaryInfo],
        transaction_infos: &[TransactionInfo],
        write_sets: &[WriteSet],
        event_vecs: &[Vec<ContractEvent>],
        begin_version: Version,
        end_version: Version,
        verify_execution_mode: &VerifyExecutionMode,
    ) -> Result<Version> {
        // Execute transactions.
        let parent_state = self.commit_queue.lock().latest_state().clone();
        let state_view = self.state_view(parent_state.latest())?;
        let txns = transactions
            .iter()
            .take((end_version - begin_version) as usize)
            .cloned()
            .map(|t| t.into())
            .collect::<Vec<SignatureVerifiedTransaction>>();

        let auxiliary_info = persisted_aux_info
            .iter()
            .take((end_version - begin_version) as usize)
            .map(|persisted_aux_info| AuxiliaryInfo::new(*persisted_aux_info, None))
            .collect::<Vec<_>>();
        let onchain_config = chunk_onchain_config(&state_view)?;
        let execution_output = DoGetExecutionOutput::by_transaction_execution::<V>(
            &V::new(),
            txns.into(),
            auxiliary_info,
            &parent_state,
            state_view,
            onchain_config,
            TransactionSliceMetadata::chunk(begin_version, end_version),
        )?;
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
        Ok(end_version)
```

**File:** storage/db-tool/src/replay_on_archive.rs (L392-406)
```rust
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

**File:** execution/executor/src/chunk_executor/chunk_result_verifier.rs (L36-66)
```rust
impl ChunkResultVerifier for StateSyncChunkVerifier {
    fn verify_chunk_result(
        &self,
        parent_accumulator: &InMemoryTransactionAccumulator,
        ledger_update_output: &LedgerUpdateOutput,
    ) -> Result<()> {
        // In consensus-only mode, we cannot verify the proof against the executed output,
        // because the proof returned by the remote peer is an empty one.
        if cfg!(feature = "consensus-only-perf-test") {
            return Ok(());
        }

        THREAD_MANAGER.get_exe_cpu_pool().install(|| {
            let first_version = parent_accumulator.num_leaves();

            // Verify the chunk extends the parent accumulator.
            let parent_root_hash = parent_accumulator.root_hash();
            let num_overlap = self.txn_infos_with_proof.verify_extends_ledger(
                first_version,
                parent_root_hash,
                Some(first_version),
            )?;
            assert_eq!(num_overlap, 0, "overlapped chunks");

            // Verify transaction infos match
            ledger_update_output
                .ensure_transaction_infos_match(&self.txn_infos_with_proof.transaction_infos)?;

            Ok(())
        })
    }
```
