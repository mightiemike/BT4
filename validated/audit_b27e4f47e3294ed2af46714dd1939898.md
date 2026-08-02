### Title
`ensure_match_transaction_info` skips checkpoint-hash comparison, letting replay-verify and chunk-verify accept a divergent state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-equivalence check used by both offline replay-verify tooling and the online chunk-executor's execution-verification path to confirm that a locally re-executed `TransactionOutput` matches a trusted/backup-provided `TransactionInfo`. It checks status, gas used, write-set hash, and event root hash, but by its own documented admission it never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that actually commit the state/hot-state/position Merkle roots into the accumulator. A backup or synced chunk whose `TransactionInfo` carries correct write-set/event/gas/status fields but a wrong checkpoint hash will pass verification even though the authenticated state root diverges from what local execution computed.

### Finding Description
`TransactionInfo` (V0/V1) commits several roots that anchor different pieces of ledger state into the transaction accumulator: `state_change_hash` (write set), `event_root_hash` (events), and, for V1, `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (state / hot-state / native-position Merkle roots at checkpoints). [1](#0-0) 

`ensure_match_transaction_info`, which is supposed to be the ground-truth cross-check between a re-executed `TransactionOutput` and an externally supplied `TransactionInfo`, validates status, gas, write-set hash and event root hash — but explicitly does **not** validate any of the checkpoint hashes, as its own trailing comment states: [2](#0-1) 

This function is invoked in the two places responsible for actually cross-checking committed `TransactionInfo` fields against fresh VM execution:

1. `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes transactions from a backup archive and calls `ensure_match_transaction_info` to decide whether the archived `TransactionInfo` (and therefore the archived accumulator/root) is trustworthy. [3](#0-2) 

2. `execution/executor/src/chunk_executor/mod.rs`'s `verify_execution`, which is used during chunk-based execution/verification (e.g. `VerifyExecutionMode`) to confirm a chunk's re-executed outputs match the `TransactionInfo`s taken from the synced/backup transaction list before they are trusted and committed. [4](#0-3) 

Because the checkpoint hashes are never compared, none of these call sites can detect a `TransactionInfo` whose `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` do not correspond to the state actually produced by local re-execution. The gap is structurally identical to the report's root cause pattern: a value used to gate/prove a state transition ("is this ledger state/root correct?") is computed/verified from an incomplete or stale signal instead of the full, current authoritative data, letting an attacker-influenced value pass a check it should fail.

### Impact Explanation
This breaks the "committed state must match VM result" and "wrong state proof/root accepted as valid" invariants named in the state-integrity gate. A restore or replay-verify run (or a chunk-executor `verify_execution` pass) that is supposed to assert the local re-computed state root equals the previously committed one can pass silently even when the state/hot-state/position root is wrong — meaning a corrupted, tampered, or buggy backup archive (or a peer supplying `TransactionInfoV1`s during chunked sync/verification flows) could have its state-checkpoint-hash field diverge from the actual VM output without detection. Since `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` gate whether these fields are populated at all, this is presently a dormant/latent gap that becomes exploitable once those features are enabled on mainnet (the TODO explicitly calls out fixing this "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), at which point the position/hot-state root — the authenticated proof-bearing anchor for that subsystem — would no longer be independently validated by replay/verify tooling.

### Likelihood Explanation
Low-to-moderate today because `TransactionInfoV0` doesn't carry these fields and the relevant features (`HOTNESS_IN_EPILOGUE`, `TRANSACTION_INFO_V1`, `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, `HOT_STATE_ROOT_IN_TXN_INFO`) are feature-flagged and not universally enabled. The code's own comment confirms the authors are aware this must be fixed before turning on `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, indicating this is an acknowledged, currently-latent hole rather than a hypothetical one. Once those flags are enabled (which the codebase is actively building toward, given the native-position/trading subsystem under active development), the exposure becomes live for any consumer of `ensure_match_transaction_info` (replay-verify, chunk verification), without requiring any additional attacker capability beyond controlling or corrupting the backup/chunk data being verified.

### Recommendation
Extend `ensure_match_transaction_info` to also assert equality of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on the `TransactionInfo` variant) against the checkpoint hashes computed from local execution/state, mirroring how `write_set_hash` and `event_root_hash` are already checked, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` are enabled on mainnet.

### Proof of Concept
Not independently exploitable yet under default mainnet configuration because `TransactionInfoV1`'s checkpoint fields are only populated when `TRANSACTION_INFO_V1`/`HOT_STATE_ROOT_IN_TXN_INFO`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS` are on. Conceptually: construct a backup/synced chunk where a transaction's write set, events, gas, and status all match genuine execution, but its `TransactionInfoV1.state_checkpoint_hash` (or `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) is replaced with an incorrect value; feed it through `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify` or `execution/executor/src/chunk_executor/mod.rs`'s `verify_execution` — `ensure_match_transaction_info` returns `Ok(())` despite the state root mismatch, because those fields are never compared.

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

**File:** types/src/transaction/mod.rs (L2352-2364)
```rust
    pub fn hot_state_checkpoint_hash(&self) -> Option<HashValue> {
        match self {
            Self::V0(_) => None,
            Self::V1(v) => v.hot_state_checkpoint_hash,
        }
    }

    pub fn position_state_checkpoint_hash(&self) -> Option<HashValue> {
        match self {
            Self::V0(_) => None,
            Self::V1(v) => v.position_state_checkpoint_hash,
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

**File:** execution/executor/src/chunk_executor/mod.rs (L648-708)
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
    }
```
