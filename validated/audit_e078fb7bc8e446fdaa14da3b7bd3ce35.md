This confirms a genuine, locally-provable integrity gap independent of the seed report. `TransactionOutput::ensure_match_transaction_info` — the sole verification routine used by both `execution/executor/src/chunk_executor/mod.rs::verify_execution` and `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` to confirm that re-executed transactions match the already-committed record — never compares the state-root fields (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) of `TransactionInfo`. It only checks status, gas used, write-set hash, and event root hash.

### Title
Replay/verify path never checks `state_checkpoint_hash`, allowing state-root divergence to pass integrity verification - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single function used to validate that a re-executed transaction's output matches an already-committed `TransactionInfo` during chunk-executor verification and replay/backup verification. It checks status, gas, write-set hash and event-root hash, but never checks the state-root fields (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`). Consequently, if a locally re-executed batch of transactions produces a different state root (Sparse Merkle Tree root / hot-state root / position-state root) than what is recorded on-chain — e.g. due to a state-commit bug, JMH update bug, or a divergent state-checkpoint hashing path — the mismatch is silently accepted as "verified."

### Finding Description
`ensure_match_transaction_info` is defined in [1](#0-0)  and asserts equality only for `status`, `gas_used`, `write_set_hash` (== `state_change_hash`), and `event_root_hash`. The code itself documents the gap with an explicit `TODO(trading-native)` comment stating that the checkpoint hashes are ignored, so "replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution" [2](#0-1) .

This function is the only verification performed in two production integrity-check paths:
1. `execution/executor/src/chunk_executor/mod.rs::verify_execution`, which re-executes a chunk of transactions and validates the output against the previously persisted `TransactionInfo`s, calling `ensure_match_transaction_info` per transaction with no additional state-root check afterward [3](#0-2) .
2. `storage/db-tool/src/replay_on_archive.rs::execute_and_verify`, the dedicated replay-verification tool for validating archived/backed-up ledger data, which likewise relies solely on `ensure_match_transaction_info` [4](#0-3) .

Since the state root (`state_checkpoint_hash`) is what actually commits the full account/resource state of the chain (produced separately by `DoStateCheckpoint`/`assemble_transaction_infos` in [5](#0-4) ), omitting it from the equality check means the state-root computation is effectively never validated by these tools — only the write-set hash (state_change_hash) and event hash are. A bug anywhere in the SMT/JMT update path, hot-state root computation, or the new position-state root path (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, `position_state_checkpoint_hash` — see `types/src/transaction/mod.rs:2452-2453`) that causes a wrong root to be computed would go completely undetected by chunk-executor verification and by `replay_on_archive`, both of which exist specifically to catch such divergences.

### Impact Explanation
This breaks the "authenticated/committed state must match the correct VM result" invariant at the verification layer. `replay_on_archive` and chunk-executor `verify_execution` are the tools operators and the protocol rely on to detect state divergence (e.g., after a bugged software upgrade, hardware corruption, or a hard-fork-inducing state-commit bug). If the state root silently diverges while these checks report success, node operators, disaster-recovery tooling, and any hard-fork detection process built on top of replay-verify will falsely believe the ledger state is consistent, while the underlying state (accounts, resources, hot-state, or position-state trees) has actually diverged from the canonical/consensus-agreed value. This is exactly the "Hard-fork-only divergence during commit, replay, restore, or proof verification" category called out as high-impact.

### Likelihood Explanation
The gap is deterministic and always present — it doesn't require an attacker, only a state-commit bug (in existing or newly introduced state-tree paths, such as the `position_state_checkpoint_hash`/hot-state feature under active development) to go undetected. The code's own `TODO(trading-native)` comment confirms the authors are aware the check is incomplete and is expected to be closed before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, but as it stands, the check is also missing for the pre-existing, unconditional `state_checkpoint_hash`/`hot_state_checkpoint_hash` fields, not just the new position-state field.

### Recommendation
Extend `TransactionOutput::ensure_match_transaction_info` (or its callers) to recompute/compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the expected `TransactionInfo` whenever those hashes are present (i.e., at checkpoint boundaries), rather than only checking write-set/event/gas/status. This requires threading through the locally computed state-checkpoint output (as `DoStateCheckpoint` already produces) into the verification call sites in `chunk_executor::verify_execution` and `replay_on_archive::execute_and_verify`.

### Proof of Concept
Not applicable as a runnable exploit — the finding is a code-level gap in a verification routine, provable by inspection: `ensure_match_transaction_info` at [1](#0-0)  never reads or compares `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()`, and its only two callers in production verification code (`chunk_executor::verify_execution`, `replay_on_archive::execute_and_verify`) do not perform any supplementary state-root comparison.

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L58-121)
```rust
    fn assemble_transaction_infos(
        to_commit: &TransactionsWithOutput,
        transaction_info_v1: bool,
        state_checkpoint_hashes: &[Option<HashValue>],
        hot_state_checkpoint_hashes: Option<&[Option<HashValue>]>,
        position_state_checkpoint_hashes: Option<&[Option<HashValue>]>,
    ) -> (Vec<TransactionInfo>, Vec<HashValue>) {
        let _timer = OTHER_TIMERS.timer_with(&["assemble_transaction_infos"]);

        (0..to_commit.len())
            .into_par_iter()
            .with_min_len(optimal_min_len(to_commit.len(), 64))
            .map(|i| {
                let txn = &to_commit.transactions[i];
                let txn_output = &to_commit.transaction_outputs[i];
                let persisted_auxiliary_info = &to_commit.persisted_auxiliary_infos[i];
                // Use the auxiliary info hash directly from the persisted info
                let auxiliary_info_hash = match persisted_auxiliary_info {
                    PersistedAuxiliaryInfo::None => None,
                    PersistedAuxiliaryInfo::V1 { .. } => {
                        Some(CryptoHash::hash(persisted_auxiliary_info))
                    },
                    PersistedAuxiliaryInfo::TimestampNotYetAssignedV1 { .. } => None,
                };
                let state_checkpoint_hash = state_checkpoint_hashes[i];
                let event_hashes = txn_output
                    .events()
                    .iter()
                    .map(CryptoHash::hash)
                    .collect::<Vec<_>>();
                let event_root_hash =
                    InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash();
                let write_set_hash = CryptoHash::hash(txn_output.write_set());
                let status = txn_output
                    .status()
                    .as_kept_status()
                    .expect("Already sorted.");
                let txn_info = if transaction_info_v1 {
                    TransactionInfo::builder_v1()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .maybe_hot_state_checkpoint_hash(
                            hot_state_checkpoint_hashes.and_then(|hot| hot[i]),
                        )
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .maybe_position_state_checkpoint_hash(
                            position_state_checkpoint_hashes.and_then(|p| p[i]),
                        )
                        .build()
                } else {
                    TransactionInfo::builder_v0()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .build()
                };
```
