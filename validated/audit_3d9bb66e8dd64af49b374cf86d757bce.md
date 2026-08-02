## Title
Replay-verify tooling silently accepts corrupted position/hot-state checkpoint roots because `TransactionOutput::ensure_match_transaction_info` never validates them - (File: `types/src/transaction/mod.rs`)

### Summary
`ensure_match_transaction_info` is the authenticated-comparison function used to check a locally re-executed `TransactionOutput` against the `TransactionInfo` recorded (and accumulator-committed) on the archive/ledger. It checks status, gas, write-set hash, and event-root hash, but explicitly skips the `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` fields that were added to `TransactionInfoV1`. The code itself documents this gap with a `TODO(trading-native)` comment, and `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` calls exactly this function as its sole correctness check, meaning the replay-verify tool can report success for a chunk whose committed position/hot-state root diverges from what local execution actually produced.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` in `types/src/transaction/mod.rs` is meant to assert that a transaction output produced by local re-execution matches the transaction info that was actually committed to the ledger accumulator (and is thus authenticated by validator signatures/state proofs). It checks:
- `status` [1](#0-0) 
- `gas_used` [2](#0-1) 
- `write_set` hash vs `state_change_hash` [3](#0-2) 
- event root hash [4](#0-3) 

but it never touches `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the very fields that carry `TransactionInfoV1`'s consensus-committed state/hot-state/position Merkle roots, as seen in the struct and format definitions: [5](#0-4) [6](#0-5) 

The function itself carries the developer's own admission of the gap: [7](#0-6) 

This function is not just internal/test code — it is the actual verification primitive used by the archive replay tool `storage/db-tool/src/replay_on_archive.rs`, which re-executes transactions from a backup and calls `ensure_match_transaction_info` as the only per-transaction correctness gate: [8](#0-7) 

Note that `expected_txn_info` (the on-chain-committed `TransactionInfo`, including any V1 checkpoint hashes) is passed in and fetched directly from the backup/ledger's transaction-info DB (`BackupHandler::get_transaction_iter`), i.e., it is the authenticated value: [9](#0-8) 

The `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` are precisely the state-integrity commitments computed by `DoStateCheckpoint::run` from real execution state (main state SMT, hot-state SMT, and the new native-position SMT): [10](#0-9) 
and are threaded into `TransactionInfoV1` at ledger-update time: [11](#0-10) 

Because `ensure_match_transaction_info` ignores these fields, a divergence between the locally re-executed position/hot-state root and the committed one (caused by, e.g., a bug in `DoStateCheckpoint::compute_position_checkpoint`'s SMT extension logic, a non-determinism in the native-position write collapsing, or corrupted archive data for that subtree) will not be flagged by `replay_on_archive`, even though the write-set and event hashes match.

### Impact Explanation
This breaks the state-integrity invariant that "committed state that differs from the correct VM result" must be detectable via authenticated re-execution/replay checks. `replay_on_archive` is one of the primary tools operators and the Aptos Labs team use to certify that an archive/backup matches correct VM execution before trusting/promoting it (e.g., for disaster recovery, forensic verification after an incident, or validating a new backup/full node bootstrap source). If the native-position (trading) or hot-state subsystem produces a wrong root — due to a bug elsewhere, storage corruption, or a malicious archive provider serving a backup with a tampered position tree — this tool will report "no failed transactions" and give false assurance that the ledger state is correct, when in fact a Merkle root bound to consensus (via `TransactionInfoV1` → accumulator → `LedgerInfo`) is wrong. This is exactly the "authenticated API or state-view output bound to the wrong version/root" failure mode called out in the state-integrity gate, surfaced through the primary tool meant to catch such divergences.

### Likelihood Explanation
Likelihood is currently constrained by the fact that `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / `HOT_STATE_ROOT_IN_TXN_INFO` and `TRANSACTION_INFO_V1` must all be enabled on-chain for these hash fields to be populated at all: [12](#0-11) [13](#0-12) 
But once these features (which are explicitly designed to eventually be enabled, per the "Lifetime: permanent" feature comments) are turned on, the gap is unconditional and silent — there is no feature-flag guard or warning suppressing detection; the check is simply absent from the code path, as the author's own TODO states. This is a real, currently-existing logic gap rather than a hypothetical exploit chain requiring privileged access; it only requires the feature to be live and a divergent root to occur.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` between `self`/locally-computed checkpoint state and `txn_info` whenever those fields are `Some` on either side (mirroring the existing `ensure!` pattern for `state_change_hash`/`event_root_hash`), and thread the locally computed checkpoint hashes into this function (or a wrapper) at the call site in `replay_on_archive.rs`/`aptos-debugger` so genuine checkpoint mismatches abort the replay with a clear error before the feature is safely turned on in production. Add a regression test that intentionally corrupts a position/hot-state write and asserts `execute_and_verify` in `replay_on_archive.rs` fails.

### Proof of Concept
1. Enable `TRANSACTION_INFO_V1`, `HOT_STATE_ROOT_IN_TXN_INFO`, and `COMPUTE_TRADING_NATIVE_STATE_ROOTS` on a test network so `TransactionInfoV1.position_state_checkpoint_hash` is populated (`types/src/block_executor/config.rs:184-187`).
2. Take a backup/archive of a chunk of transactions including a native-position write and checkpoint.
3. Corrupt (or simulate a bug causing) the persisted `position_state_checkpoint_hash` in the archived `TransactionInfo` for one version, while keeping `write_set`, events, gas, and status identical to a correct replay.
4. Run `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::verify` against this backup.
5. Observe that `execute_and_verify` calls `ensure_match_transaction_info` (`storage/db-tool/src/replay_on_archive.rs:392-397`), which never inspects `position_state_checkpoint_hash`/`hot_state_checkpoint_hash`/`state_checkpoint_hash`, so no error is returned and the tool reports the range as verified, despite the archived, consensus-bound position-state root being wrong.

### Citations

**File:** types/src/transaction/mod.rs (L2148-2157)
```rust
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
```

**File:** types/src/transaction/mod.rs (L2159-2166)
```rust
        ensure!(
            self.gas_used() == txn_info.gas_used(),
            "{}: version:{}, gas_used:{:?}, expected:{:?}",
            ERR_MSG,
            version,
            self.gas_used(),
            txn_info.gas_used(),
        );
```

**File:** types/src/transaction/mod.rs (L2168-2178)
```rust
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
```

**File:** types/src/transaction/mod.rs (L2180-2195)
```rust
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
```

**File:** types/src/transaction/mod.rs (L2196-2203)
```rust

        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
```

**File:** types/src/transaction/mod.rs (L2461-2494)
```rust
}

impl TransactionInfoV1 {
    pub fn new(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        hot_state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
        position_state_checkpoint_hash: Option<HashValue>,
    ) -> Self {
        Self {
            gas_used,
            status,
            transaction_hash,
            event_root_hash,
            state_change_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
            placeholder1: None,
            placeholder2: None,
            placeholder3: None,
            placeholder4: None,
            placeholder5: None,
            placeholder6: None,
            placeholder7: None,
        }
    }
}
```

**File:** testsuite/generate-format/tests/staged/api.yaml (L1078-1101)
```yaml
TransactionInfoV1:
  STRUCT:
    - gas_used: U64
    - status:
        TYPENAME: ExecutionStatus
    - transaction_hash:
        TYPENAME: HashValue
    - event_root_hash:
        TYPENAME: HashValue
    - state_change_hash:
        TYPENAME: HashValue
    - state_checkpoint_hash:
        OPTION:
          TYPENAME: HashValue
    - hot_state_checkpoint_hash:
        OPTION:
          TYPENAME: HashValue
    - auxiliary_info_hash:
        OPTION:
          TYPENAME: HashValue
    - position_state_checkpoint_hash:
        OPTION:
          TYPENAME: HashValue
    - placeholder1:
```

**File:** storage/db-tool/src/replay_on_archive.rs (L373-405)
```rust
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
```

**File:** storage/aptosdb/src/backup/backup_handler.rs (L103-132)
```rust
        let zipped = txn_iter.enumerate().map(move |(idx, txn_res)| {
            let version = start_version + idx as u64; // overflow is impossible since it's check upon txn_iter construction.

            let txn = txn_res?;
            let txn_info = txn_info_iter.next().ok_or_else(|| {
                AptosDbError::NotFound(format!(
                    "TransactionInfo not found when Transaction exists, version {}",
                    version
                ))
            })??;
            let event_vec = event_vec_iter.next().ok_or_else(|| {
                AptosDbError::NotFound(format!(
                    "Events not found when Transaction exists., version {}",
                    version
                ))
            })??;
            let write_set = write_set_iter.next().ok_or_else(|| {
                AptosDbError::NotFound(format!(
                    "WriteSet not found when Transaction exists, version {}",
                    version
                ))
            })??;
            let persisted_aux_info = persisted_aux_info_iter.next().ok_or_else(|| {
                AptosDbError::NotFound(format!(
                    "PersistedAuxiliaryInfo not found when Transaction exists, version {}",
                    version
                ))
            })??;
            BACKUP_TXN_VERSION.set(version as i64);
            Ok((txn, persisted_aux_info, txn_info, event_vec, write_set))
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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L95-121)
```rust
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

**File:** types/src/block_executor/config.rs (L173-189)
```rust
    pub fn with_features(mut self, features: &Features) -> Self {
        self.hotness_in_epilogue = features.is_hotness_in_epilogue_enabled();
        self.transaction_info_v1 = features.is_transaction_info_v1_enabled();
        // Requires transaction_info_v1: the hot state root rides in
        // TransactionInfoV1's hot_state_checkpoint_hash field, which V0 lacks.
        self.hot_state_root_in_txn_info = features.is_hot_state_root_in_txn_info_enabled()
            && features.is_transaction_info_v1_enabled();
        // Requires transaction_info_v1 (the root rides in TransactionInfoV1) and
        // hotness_in_epilogue (only the V1 write-set format it enables serializes
        // the native-position extensions; V0 drops them, so output-replay would
        // diverge). Degrades to off if either is missing.
        self.compute_trading_native_state_roots = features
            .is_compute_trading_native_state_roots_enabled()
            && features.is_transaction_info_v1_enabled()
            && features.is_hotness_in_epilogue_enabled();
        self
    }
```

**File:** aptos-move/framework/move-stdlib/sources/configs/features.move (L949-961)
```text
    /// When enabled, execution computes the trading-native state roots and commits them to
    /// `TransactionInfoV1`, so they are consensus-verified. Requires `TRANSACTION_INFO_V1`.
    /// Covers the native-position tree today and is intended to cover the other trading-native
    /// trees as they are added. Enabling it first commits the (empty-tree) roots to transaction
    /// info; the actual Move-side writes to those trees are gated by separate flags.
    /// Lifetime: permanent
    const COMPUTE_TRADING_NATIVE_STATE_ROOTS: u64 = 122;

    /// When enabled together with `TRANSACTION_INFO_V1`, execution populates
    /// `TransactionInfoV1`'s hot state root hash, so it is committed to the ledger
    /// accumulator. Requires `TRANSACTION_INFO_V1`.
    /// Lifetime: permanent
    const HOT_STATE_ROOT_IN_TXN_INFO: u64 = 123;
```
