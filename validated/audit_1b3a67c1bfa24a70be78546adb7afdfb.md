### Title
`TransactionOutput::ensure_match_transaction_info` never validates `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`, letting replay-verify and backup-restore execution-verification pass with a silently divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by all "execution verification" tooling (chunk-executor replay verification during backup restore, `db-tool`'s `replay_on_archive`/`replay_verify`, and the `aptos move` CLI/debugger replay) to confirm that locally re-executed transactions match the transaction infos that were authenticated by consensus and stored in the accumulator. It checks status, gas, write-set hash, and event root hash, but it never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the actual state-tree/hot-state/native-position root commitments carried in `TransactionInfoV1`/`V0`. This is confirmed by the code itself in a `TODO(trading-native)` comment.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  compares only:
- kept/discard status vs `txn_info.status()`
- `gas_used()` vs `txn_info.gas_used()`
- `CryptoHash::hash(self.write_set())` vs `txn_info.state_change_hash()`
- event root hash vs `txn_info.event_root_hash()`

It explicitly does **not** compare `txn_info.state_checkpoint_hash()` (the Sparse-Merkle/Jellyfish-Merkle state root committed periodically per block), nor the newer `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()` fields introduced in `TransactionInfoV1` [2](#0-1) . The comment left in the code says verbatim: *"this comparator ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."*

This function is the sole per-transaction correctness check in several security-relevant flows:
- `ChunkExecutorInner::verify_execution`, used during backup restore with `VerifyExecutionMode::Verify` [3](#0-2) , called from `remove_and_replay_epoch`/`enqueue_chunks` which back `TransactionReplayer` used by `TransactionRestoreBatchController` (backup-cli restore/verify flows) and `db-tool`'s `replay_on_archive.rs` (`execute_and_verify`) [4](#0-3) .
- `aptos-debugger`/CLI transaction replay (`aptos-move/cli/src/commands.rs`) which reports a transaction as verified via the same call [5](#0-4) .

Because these tools never check the state-tree root fields, an operator relying on `replay_verify`/`replay_on_archive`/backup-restore-with-verification as an integrity oracle for "does re-execution reproduce the exact committed ledger state" gets a false "verification succeeded" result even when the recomputed state root (state_checkpoint_hash), hot-state root, or native-position root differs from what is embedded (and accumulator-committed) in the archived `TransactionInfo`. Note this is separate from the actual consensus/state-sync commit path, which uses `LedgerUpdateOutput::ensure_transaction_infos_match` (a different, stricter comparator) — so this bug does not corrupt live consensus state; its blast radius is confined to the offline verification/audit tooling that is explicitly meant to catch such divergences.

### Impact Explanation
Under the state-integrity gate, this qualifies as a proof/commitment-integrity gap in a **restore/replay verification** path: the tool whose entire purpose is to detect "wrong accumulator root / wrong state-checkpoint hash accepted as valid during replay" fails to detect exactly that for the state, hot-state, and native-position roots. This is especially concerning now that `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / `HOT_STATE_ROOT_IN_TXN_INFO` features exist and are meant to be "consensus-verified" per their own doc comments [6](#0-5) , yet the offline audit tool that operators use to validate archived/replayed history for exactly these roots silently skips the check. An operator restoring from backup with `VerifyExecutionMode::verify_all()`/`verify_except(...)` (used by `replay_verify` and transaction-backup tests [7](#0-6) ) would get a false positive "successful replay" even if the restored/replayed state root is wrong, undermining confidence in backup integrity checks and any downstream decision to trust a restored DB.

### Likelihood Explanation
This is a deterministic, code-path-guaranteed gap (not a race or timing issue): any divergence in state_checkpoint_hash, hot_state_checkpoint_hash, or position_state_checkpoint_hash between local re-execution and the archived transaction info will always be missed by every caller of `ensure_match_transaction_info`, given how simple it is to trigger. It requires no attacker interaction against consensus — it's an inherent silent-blind-spot in the verification tool triggered by any actual state-root divergence (bug in an on-chain module, an accidental behavior change in the native-position/hot-state feature, a bug in restore, a corrupted DB, etc.). The comment in the code confirms the authors are already aware of the gap for `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, but the state_checkpoint_hash (base state root, unrelated to the new trading-native feature) is likewise never checked, and that's been true independent of the new feature flags.

### Recommendation
Extend `ensure_match_transaction_info` to also validate, when both sides carry a value:
- `txn_info.state_checkpoint_hash()` against a caller-provided/locally-computed state-checkpoint hash for the relevant checkpoint boundary,
- `txn_info.hot_state_checkpoint_hash()` when `HOT_STATE_ROOT_IN_TXN_INFO` is enabled,
- `txn_info.position_state_checkpoint_hash()` when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled,

before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` on mainnet, as the existing TODO comment already recommends, and update `replay_on_archive`, `ChunkExecutorInner::verify_execution`, and the CLI debugger call sites to pass through the checkpoint hashes computed by `DoStateCheckpoint` for comparison.

### Proof of Concept
1. Enable `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` (or simply consider any historical divergence in the base `state_checkpoint_hash`).
2. Take an archived transaction whose `TransactionInfoV1.position_state_checkpoint_hash` (or `state_checkpoint_hash`) was computed with a different native-position/state root than what local re-execution now produces (e.g., due to a state-root computation bug introduced elsewhere, or corrupted archive/backup data).
3. Run `db-tool replay-on-archive` or restore with `VerifyExecutionMode::verify_all()` over that version range: `Verifier::execute_and_verify` calls `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_infos[idx], Some(&expected_writesets[idx]), Some(&expected_events[idx]))` [8](#0-7) .
4. Because `ensure_match_transaction_info` never compares the state/hot-state/position checkpoint hash fields [2](#0-1) , the call returns `Ok(())` and the tool reports the replay as verified/successful, even though the state root diverges from the one embedded (and accumulator-committed) in the archived `TransactionInfo`.

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

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```

**File:** types/src/on_chain_config/aptos_features.rs (L203-209)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
    /// When enabled, execution populates `TransactionInfoV1`'s hot state root hash, so it
    /// is committed to the ledger accumulator. Requires `TRANSACTION_INFO_V1`.
    HOT_STATE_ROOT_IN_TXN_INFO = 123,
```

**File:** storage/db-tool/src/replay_verify.rs (L74-87)
```rust
        let ret = ReplayVerifyCoordinator::new(
            self.storage.init_storage().await?,
            self.metadata_cache_opt,
            self.trusted_waypoints_opt,
            self.concurrent_downloads.get(),
            self.replay_concurrency_level.get(),
            restore_handler,
            self.start_version.unwrap_or(0),
            self.end_version.unwrap_or(Version::MAX),
            self.validate_modules,
            VerifyExecutionMode::verify_except(self.txns_to_skip).set_lazy_quit(self.lazy_quit),
        )?
        .run()
        .await;
```
