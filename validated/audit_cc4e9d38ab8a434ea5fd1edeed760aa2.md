### Title
`ensure_match_transaction_info` skips state/hot-state/position checkpoint hash comparison, allowing replay-verify to accept an authenticated `TransactionInfo` whose committed state root diverges from local execution - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single integrity check used by both state-sync chunk verification (`execution/executor/src/chunk_executor/mod.rs::verify_execution`) and the `replay-verify` / `replay_on_archive` tooling (`storage/db-tool/src/replay_on_archive.rs::execute_and_verify`) to confirm that a locally re-executed `TransactionOutput` matches an authenticated `TransactionInfo` obtained from a proof/backup source. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
The function only checks `status`, `gas_used`, `write_set_hash` (`state_change_hash`), and `event_root_hash` against the given `TransactionInfo`: [4](#0-3) 

It never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that authenticate the Jellyfish-Merkle state root / hot-state / position tree committed alongside the transaction. This gap is explicitly acknowledged in a TODO comment left in the code: [5](#0-4) 

Because `TransactionInfoV1` carries these checkpoint hash fields (`maybe_state_checkpoint_hash`, `maybe_hot_state_checkpoint_hash`, `maybe_position_state_checkpoint_hash`) as part of its authenticated content that is itself hashed into the transaction accumulator, an authenticated `TransactionInfo` with a checkpoint hash that does not match the state actually produced by local re-execution will still pass `ensure_match_transaction_info` — as long as the write-set hash, event root, status and gas used happen to match. The only observable field validated is derived purely from the write set/events, not the resulting state Merkle root, so no invariant currently ties the *committed state root* to the locally-recomputed state.

### Impact Explanation
This check is relied upon by two integrity-critical flows:
1. `ChunkExecutor::verify_execution` during state-sync chunk verification, which is meant to guarantee a downloaded chunk of transactions actually reproduces the claimed ledger state. [6](#0-5) 
2. `db-tool replay-verify` (`replay_on_archive.rs`), whose entire purpose is to detect divergence between a backed-up/archived ledger history and independent re-execution — the exact kind of authenticated-proof/restore-flow validation this scan targets. [7](#0-6) 

Because the state checkpoint hash is never re-validated, a divergence that is confined to the state tree (e.g. from an execution/storage bug that produces a different final state but the same write set/events/status/gas — for instance a state-checkpoint hashing bug, hot-state materialization bug, or position-tree divergence introduced by an upgrade) would be silently accepted as "verified" by replay-verify tooling, even though the resulting Merkle state root committed to the accumulator differs from the correct VM result. This directly matches the "authenticated API or proof-bearing response bound to the wrong root" class: replay-verify is the authenticated tool operators/auditors trust to catch exactly this kind of corruption, and it is blind to it.

### Likelihood Explanation
The comment shows the gap is known and intentionally deferred pending an unlanded feature flag (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`), meaning the checkpoint-hash divergence detection is currently disabled on all call sites of `ensure_match_transaction_info`, not gated behind any experimental config. Any state-only divergence bug elsewhere in the storage/state-checkpoint pipeline would go undetected by this specific safety net. However, this is a detection/verification gap rather than a direct forgeable state-corruption primitive by itself — it requires a separate root-cause divergence (e.g. a state-checkpoint or hot-state computation bug) to actually produce incorrect committed state; this check's weakness only means such a bug would not be caught by replay-verify.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` between the locally computed checkpoint (passed in, or recomputed from the resulting state view) and the value carried in `txn_info`, as the existing TODO already recommends, rather than leaving this validation disabled across all callers (state-sync chunk verification and replay-verify).

### Proof of Concept
Not applicable as a standalone exploit — this is a verification-gap finding. Local proof consists of:
1. `ensure_match_transaction_info` definition validating only status/gas/write_set_hash/event_root_hash: [8](#0-7) 
2. Its use as the sole match-check in chunk-executor verify path: [2](#0-1) 
3. Its use as the sole match-check in `replay_on_archive`'s `execute_and_verify`: [3](#0-2) 

Note: I could not fully trace whether any other independent check (outside these two call sites) re-validates the state checkpoint hash during chunk-sync or replay-verify — the index does not show a broader corroborating check, but a full audit of `execution/executor/src/chunk_executor/mod.rs` and the state-sync driver beyond what was retrieved would be needed to rule out a compensating control elsewhere.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2203)
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
```

**File:** execution/executor/src/chunk_executor/mod.rs (L648-706)
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
```

**File:** storage/db-tool/src/replay_on_archive.rs (L349-415)
```rust
    fn execute_and_verify(
        &self,
        executor: &AptosVMBlockExecutor,
        current_version: &mut Version,
        cur_txns: &mut Vec<Transaction>,
        cur_persisted_aux_info: &mut Vec<PersistedAuxiliaryInfo>,
        expected_txn_infos: &mut Vec<TransactionInfo>,
        expected_events: &mut Vec<Vec<ContractEvent>>,
        expected_writesets: &mut Vec<WriteSet>,
    ) -> Result<Option<Error>> {
        if cur_txns.is_empty() {
            return Ok(None);
        }
        let txns = cur_txns
            .iter()
            .map(|txn| SignatureVerifiedTransaction::from(txn.clone()))
            .collect::<Vec<_>>();
        let txns_provider = DefaultTxnProvider::new(
            txns,
            cur_persisted_aux_info
                .iter()
                .map(|info| AuxiliaryInfo::new(*info, None))
                .collect(),
        );
        let executed_outputs = executor
            .execute_block(
                &txns_provider,
                &self
                    .arc_db
                    .state_view_at_version(current_version.checked_sub(1))?,
                BlockExecutorConfigFromOnchain::new_no_block_limit(), // TODO(HotState): will need to incorporate some features.
                TransactionSliceMetadata::Chunk {
                    begin: *current_version,
                    end: *current_version + cur_txns.len() as u64,
                },
            )
            .map(BlockOutput::into_transaction_outputs_forced)?;
        assert_eq!(executed_outputs.len(), cur_txns.len());

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
        }

        cur_txns.clear();
        cur_persisted_aux_info.clear();
        expected_txn_infos.clear();
        expected_events.clear();
        expected_writesets.clear();

        Ok(None)
    }
```
