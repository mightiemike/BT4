## Finding

### Title
Replay‑verify never checks the state‑checkpoint (JMT) root, letting divergent state Merklization silently pass validation - (`types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the core comparator used by every "replay and verify" path in Aptos (backup restore verification, `db-tool replay_on_archive`, and the chunk executor's `verify_execution`) to confirm that a locally re-executed transaction produces the same result as the transaction info already committed and accumulator-proven on chain. It checks status, gas, the write-set hash (`state_change_hash`) and the event root hash — but it never checks `state_checkpoint_hash` (nor `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`), which is the actual Jellyfish Merkle Tree state root produced by applying the write set to the accumulated ledger state.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  validates only:
- execution status vs. `txn_info.status()`
- `gas_used`
- `write_set_hash == txn_info.state_change_hash()`
- `event_root_hash == txn_info.event_root_hash()`

It explicitly skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, with the code itself documenting the gap: [2](#0-1) 

This function is the sole result-verification primitive used by:
- `execution/executor/src/chunk_executor/mod.rs`'s `verify_execution`, invoked from `remove_and_replay_epoch`, part of the production transaction-replay path used when a node restores/replays from backup with verification enabled: [3](#0-2) 
- `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, the dedicated tool operators/infra run to confirm historical replay determinism against archived data: [4](#0-3) 
- `storage/backup/backup-cli/src/backup_types/transaction/restore.rs` (via `VerifyExecutionMode`), the backup-restore verification flow.

The `state_checkpoint_hash` is the JMT root committed into `TransactionInfo` and, transitively, into the transaction accumulator whose root is validator-signed in `LedgerInfo`. It is the strongest state-integrity signal available — stronger than the per-transaction write-set hash, because it reflects the cumulative effect of Merklizing that write set into the whole state tree (including hot-state promotion, sharding, and — once enabled — the trading-native position tree). None of these mechanisms are exercised by `ensure_match_transaction_info`.

Consequently, any bug in the state-Merklization pipeline (JMT construction/update logic in `execution/executor/src/workflow/do_state_checkpoint.rs`, hot-state promotion, or the newer position-state tree in `compute_position_checkpoint`, see [5](#0-4) ) that produces a different root hash than the canonical chain — while still producing byte-identical write sets, events, gas, and status — would pass `ensure_match_transaction_info` with a wholesale `Ok(())`, even though the two state trees have actually diverged.

### Impact Explanation
This breaks the state-commitment/proof-integrity gate: "committed state that differs from the correct VM result" and "hard-fork-only divergence during commit, replay, restore, or proof verification" are exactly what replay-verify and backup-restore-verify exist to catch. Because the root-hash check is missing, replay-verify tooling can report a clean, successful verification of an entire epoch of historical data while the locally computed state root has silently diverged from the authenticated chain state. This directly undermines the primary safety net operators rely on before trusting a restored/replayed database, and — as the code comment itself warns — is a prerequisite gap that must be closed before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is turned on, since that feature's entire purpose is to make the position-state root consensus-verified.

### Likelihood Explanation
The defect is unconditional (not gated behind any feature flag) for the base `state_checkpoint_hash` field — it applies to every state-checkpoint transaction replayed through any of the three call sites listed above, on every version of Aptos that ships this code. Any latent non-determinism or bug in the JMT/hot-state Merklization logic (a class of bug that has historically occurred in Merkle-tree implementations) would be undetectable by the very tool designed to catch it, with no attacker action required beyond the pre-existing bug being triggered by ordinary transaction replay.

### Recommendation
Extend `ensure_match_transaction_info` to accept and compare the expected `state_checkpoint_hash` (and, once relevant, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against the locally recomputed state-checkpoint output for checkpoint transactions, and thread that expected value through the three call sites (`chunk_executor::verify_execution`, `replay_on_archive::execute_and_verify`, and the backup-cli restore verifier) before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any reliance on replay-verify for state-root correctness) is used in production.

### Proof of Concept
Not directly exploitable by an external attacker without a pre-existing state-Merklization bug; the vulnerability is demonstrated purely by code inspection:
1. Introduce (or trigger, e.g. via a hot-state promotion edge case) a divergence in `DoStateCheckpoint::run` such that the computed `state_summary.last_checkpoint().root_hash()` differs from the canonical chain's `state_checkpoint_hash` at some version, while all write sets/events/status/gas remain identical.
2. Run `db-tool replay_on_archive` over that version range.
3. `execute_and_verify` calls `ensure_match_transaction_info`, which validates only status/gas/write_set_hash/event_root_hash and returns `Ok(())` despite the state roots differing — the divergence is never reported: [6](#0-5)

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

**File:** storage/db-tool/src/replay_on_archive.rs (L388-406)
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
        }
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L36-83)
```rust
        let state_summary = parent_state_summary.update(
            persisted_state_summary,
            &execution_output.hot_state_updates,
            execution_output.to_commit.state_update_refs(),
        )?;

        let last_checkpoint = state_summary.last_checkpoint();

        let state_checkpoint_hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_state_checkpoints,
            last_checkpoint.root_hash(),
            "state",
        )?;
        let hot_state_checkpoint_hashes = execution_output
            .hot_state_root_in_txn_info
            .then(|| {
                Self::get_state_checkpoint_hashes(
                    execution_output,
                    known_hot_state_checkpoints,
                    last_checkpoint.hot_root_hash()?,
                    "hot_state",
                )
            })
            .transpose()?;

        let (position_state_summary, position_state_checkpoint_hashes) =
            if execution_output.compute_trading_native_state_roots {
                let persisted = persisted_position_state_summary
                    .expect("persisted position summary required when feature on");
                let (summary, hashes) = Self::compute_position_checkpoint(
                    execution_output,
                    parent_position_state_summary,
                    persisted,
                    known_position_state_checkpoints,
                )?;
                (Some(summary), Some(hashes))
            } else {
                (None, None)
            };

        Ok(StateCheckpointOutput::builder()
            .state_summary(state_summary)
            .state_checkpoint_hashes(state_checkpoint_hashes)
            .maybe_hot_state_checkpoint_hashes(hot_state_checkpoint_hashes)
            .maybe_position_state_summary(position_state_summary)
            .maybe_position_state_checkpoint_hashes(position_state_checkpoint_hashes)
            .build())
```
