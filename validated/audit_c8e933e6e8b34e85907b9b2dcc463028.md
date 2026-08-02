### Title
`ensure_match_transaction_info` skips checkpoint-hash comparison, letting replay-verify tooling accept a committed `TransactionInfo` whose position/state checkpoint roots diverge from local execution - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by chunk-executor replay verification, `db-tool`'s `replay_on_archive`, and the CLI transaction simulator to confirm that a locally re-executed transaction output matches the authenticated `TransactionInfo` recorded on-chain. Its own inline `TODO(trading-native)` comment documents that it deliberately does **not** compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — only status, gas, write-set hash, and event root hash are checked.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` at [1](#0-0)  checks status, gas used, `state_change_hash` (write-set hash), and `event_root_hash`, but explicitly omits any comparison of the state/hot-state checkpoint hash and the newly added `position_state_checkpoint_hash` field carried by `TransactionInfoV1` [2](#0-1) . The comment states verbatim: "this comparator ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is called directly by:
- The chunk executor's execution-verification path, `verify_execution`, which is the core mechanism used to re-validate historical execution during state-sync/backup verification [3](#0-2) .
- `db-tool`'s `replay_on_archive::execute_and_verify`, the tool operators run to certify that an archived/replayed chain matches the authenticated on-chain `TransactionInfo` [4](#0-3) .
- `aptos-debugger`/CLI transaction replay comparison [5](#0-4) .

`position_state_checkpoint_hash` is a new, permanent, committed field on `TransactionInfoV1` — gated by the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature (feature id 122) — that is meant to be "consensus-verified" once turned on [6](#0-5) . The field is populated at execution time from the native-position Jellyfish Merkle tree summary [7](#0-6)  and is consumed downstream as an authenticated proof-binding value, e.g. state-sync's bootstrapper trusts `position_state_checkpoint_hash()` straight from the target `TransactionInfo` as the expected snapshot root for the position-state fast-sync stage [8](#0-7) .

This is the direct analog of the external bug's root cause: a new committed/verification-relevant field is introduced, but the corresponding integrity-check code path was not updated to validate it, so a class of divergence — here, the position-state root diverging from re-executed data — silently passes verification.

### Impact Explanation
`replay_on_archive`/replay-verify is the tool operators rely on to detect if an archived or replayed ledger deviates from the authenticated chain (a hard-fork/consensus-divergence detector). Because `ensure_match_transaction_info` never compares `position_state_checkpoint_hash` (nor `state_checkpoint_hash`/`hot_state_checkpoint_hash`), a corrupted, buggy, or maliciously-altered native-position JMT computation (or a storage/restore bug that reinterprets committed position data) would not be flagged by replay verification, even though the committed `TransactionInfoV1.position_state_checkpoint_hash` — a value that is bound into the transaction accumulator and hence into the ledger info signed by validators — is wrong relative to locally recomputed state. This directly matches the "authenticated API/proof-bearing output bound to the wrong ... root" and "restore/replay paths must not reinterpret committed data into a different ledger state" invariants called out as in-scope. Impact is currently gated (the feature flag `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is off and `ENABLE_TRADING_NATIVE` is `false` in this codebase, per `storage/aptosdb/src/trading_native.rs`), but the check gap exists in code today and is a landmine for when the feature ships — a silent-corruption verification bypass is High severity by nature (it defeats the archive/replay integrity guarantee the tool exists to provide) even though current exploitability is limited by the feature being disabled.

### Likelihood Explanation
Likelihood is Medium: the gap is real and already merged, is explicitly flagged as a known gap by the developers themselves (the TODO), and will become live and actively depended upon the moment `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`ENABLE_TRADING_NATIVE` are turned on for the native-trading rollout. No malicious/privileged action is required to trigger the underlying divergence-detection failure; it's an internal-consistency check omission, not a permission-gated feature.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the recomputed/authoritative equivalents (or explicitly assert they are `None` when the corresponding features are disabled), exactly as the existing TODO instructs, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled on any network. This should be validated by the chunk-executor's `verify_execution` and `db-tool`'s `replay_on_archive`, both of which rely on this comparator as their sole state-correctness oracle.

### Proof of Concept
Not independently exploitable today because `ENABLE_TRADING_NATIVE` is hard-coded `false` [9](#0-8)  and `COMPUTE_TRADING_NATIVE_STATE_ROOTS` requires `TRANSACTION_INFO_V1` + `HOTNESS_IN_EPILOGUE` to be simultaneously on [10](#0-9) . Once those flags are enabled: construct a `TransactionInfoV1` with a tampered/incorrect `position_state_checkpoint_hash` (e.g. by corrupting the on-disk native-position JMT or replaying against a divergent implementation) and call `ensure_match_transaction_info` — it returns `Ok(())` despite the checkpoint-hash mismatch, demonstrated directly by the code path shown at `types/src/transaction/mod.rs:2196-2203`.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L692-705)
```rust
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

**File:** types/src/on_chain_config/aptos_features.rs (L203-206)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L35-45)
```rust
        let (transaction_infos, transaction_info_hashes) = Self::assemble_transaction_infos(
            &execution_output.to_commit,
            execution_output.transaction_info_v1,
            &state_checkpoint_output.state_checkpoint_hashes,
            state_checkpoint_output
                .hot_state_checkpoint_hashes
                .as_deref(),
            state_checkpoint_output
                .position_state_checkpoint_hashes
                .as_deref(),
        );
```

**File:** state-sync/state-sync-driver/src/bootstrapper.rs (L981-1008)
```rust
    /// The expected snapshot root for the given kind at the target version, read
    /// from the target transaction info: main state's state checkpoint hash, or
    /// the committed position state root (guaranteed present once the position
    /// stage runs, per `snapshot_kind_applies_to_target`). All kinds share the
    /// target version, so this is taken from the target output, not a storage read.
    fn expected_snapshot_root(&mut self, kind: StateKind) -> Result<HashValue, Error> {
        let transaction_output_to_sync = self.get_transaction_output_to_sync()?;
        let target_transaction_info = transaction_output_to_sync
            .get_output_list_with_proof()
            .proof
            .transaction_infos
            .first()
            .ok_or_else(|| {
                Error::UnexpectedError("Target transaction info does not exist!".into())
            })?;
        match kind {
            StateKind::MainState => target_transaction_info
                .ensure_state_checkpoint_hash()
                .map_err(|error| {
                    Error::UnexpectedError(format!(
                        "State checkpoint must exist! Error: {:?}",
                        error
                    ))
                }),
            StateKind::Position => target_transaction_info
                .position_state_checkpoint_hash()
                .ok_or_else(|| Error::UnexpectedError("Missing position state root!".into())),
        }
```

**File:** storage/aptosdb/src/trading_native.rs (L9-10)
```rust
/// Flip to `true` once order/collateral land.
pub(crate) const ENABLE_TRADING_NATIVE: bool = false;
```

**File:** types/src/block_executor/config.rs (L180-188)
```rust
        // Requires transaction_info_v1 (the root rides in TransactionInfoV1) and
        // hotness_in_epilogue (only the V1 write-set format it enables serializes
        // the native-position extensions; V0 drops them, so output-replay would
        // diverge). Degrades to off if either is missing.
        self.compute_trading_native_state_roots = features
            .is_compute_trading_native_state_roots_enabled()
            && features.is_transaction_info_v1_enabled()
            && features.is_hotness_in_epilogue_enabled();
        self
```
