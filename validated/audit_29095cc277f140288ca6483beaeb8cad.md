I found a genuine, self-documented integrity gap in this fork's transaction-output verification code, distinct from the Solidity report but matching the same bug class (a validation gate that is missing/bypassable, letting a bad state persist).

### Title
Missing state/hot-state/position-state checkpoint hash validation in `TransactionOutput::ensure_match_transaction_info` lets replay-verify, chunk-executor verification, and CLI replay accept a diverged state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single authenticated-output validator used by every path in this codebase that checks a freshly executed `TransactionOutput` against a trusted, consensus/backup-provided `TransactionInfo`. It checks status, gas, write-set hash, and event root hash, but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, and the code explicitly documents this omission as a known-but-unfixed gap.

### Finding Description [1](#0-0) 

`ensure_match_transaction_info` verifies `status`, `gas_used`, the write-set hash against `txn_info.state_change_hash()`, and the event root hash against `txn_info.event_root_hash()`. It then returns `Ok(())` with only a comment: [2](#0-1) 

The comment itself states the check "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`)", meaning the JMT state-checkpoint root, the hot-state root, and the new trading-native position-state root committed inside `TransactionInfoV1` are never independently recomputed and compared during this validation.

This single function is reused as the sole integrity gate in every consumer that is supposed to catch execution/storage divergence:
- Chunk executor state-sync verification: [3](#0-2) 
- `db-tool replay-on-archive`, whose entire purpose is to re-execute an archived chain and flag any divergence from the recorded `TransactionInfo`: [4](#0-3) 
- CLI transaction replay comparison: [5](#0-4) 
- The debugger's mismatch printer: [6](#0-5) 

Meanwhile, the checkpoint hashes are real, consensus/backup-carried fields that are supposed to bind the ledger's Merkle/JMT state root (and, when the fork's new `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature is on, the position-state root) to each `TransactionInfoV1`: [7](#0-6)  and [8](#0-7) .

### Impact Explanation
Because `ensure_match_transaction_info` never recomputes/compares these checkpoint hashes, any of the following silently pass verification:
- A backup archive (untrusted `BackupStorage`) whose `TransactionInfo.state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` do not match what local re-execution actually produces — `replay-on-archive`/`replay-verify` will report success even though the locally committed state root diverges from the archive's/authenticated ledger's root.
- A chunk-executor `verify_execution_mode` state-sync pass that re-executes a chunk and is meant to catch any hard-fork-style state divergence — it will not detect a state-root mismatch as long as write set and events line up.
- Once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, a corrupted/incorrect position-state root committed to `TransactionInfoV1` is never cross-checked, so tooling built to guard "authenticated position state root diverges from local execution" (the tool's own stated purpose) cannot actually do so.

This satisfies the state-integrity gate: it is a proof/checkpoint-binding invariant ("committed root must equal locally recomputed root") that is silently unenforced by the exact tools whose job is to enforce it during replay/restore/commit verification.

### Likelihood Explanation
Low-to-moderate: this requires either (a) a bug elsewhere in JMT/state-checkpoint or position-state construction that produces a wrong root while write-set/events still match (plausible, since write-set hash and checkpoint hash are computed by different code paths — `WriteSet::hash` vs `DoStateCheckpoint`/JMT), or (b) an untrusted/corrupted backup archive. The gap is not itself directly triggerable by an unprivileged transaction, but it removes the safety net that is supposed to catch state-root corruption from any source, which is exactly the scenario replay-verify tooling exists for.

### Recommendation
Extend `ensure_match_transaction_info` to also assert `txn_info.state_checkpoint_hash()` (and, when applicable, `hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()`) equal the locally recomputed checkpoint hashes before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, as the code's own TODO already recommends, and audit all four call sites (`chunk_executor`, `replay_on_archive`, CLI replay, debugger) to ensure they pass the locally computed checkpoint hash(es) into the check.

### Proof of Concept
1. Enable `TRANSACTION_INFO_V1` (and, to exercise the position path, `COMPUTE_TRADING_NATIVE_STATE_ROOTS`).
2. Run `db-tool replay-on-archive` (or the chunk-executor `verify_execution` path) against an archive/backup where the `TransactionInfo.state_checkpoint_hash`/`position_state_checkpoint_hash` has been altered (e.g., corrupted metadata, or a bug that produces a wrong JMT root) while write set, events, gas, and status remain identical.
3. Observe `ensure_match_transaction_info` at [9](#0-8)  returns `Ok(())`, and the tool reports a successful replay/verification despite the state root being wrong.

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

**File:** aptos-move/cli/src/commands.rs (L2805-2813)
```rust
        // When local package overrides are in use the replayed code diverges from
        // what was originally executed on-chain (different instructions, gas, etc.),
        // so output comparison is meaningless and is automatically skipped.
        let skip_comparison = self.skip_comparison || !self.use_local_package.is_empty();
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L233-246)
```rust
    fn print_mismatches(
        txn_outputs: &[TransactionOutput],
        expected_txn_infos: &[TransactionInfo],
        first_version: Version,
    ) {
        for idx in 0..txn_outputs.len() {
            let txn_output = &txn_outputs[idx];
            let txn_info = &expected_txn_infos[idx];
            let version = first_version + idx as Version;
            txn_output
                .ensure_match_transaction_info(version, txn_info, None, None)
                .unwrap_or_else(|err| println!("{}", err))
        }
    }
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L44-83)
```rust
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

**File:** aptos-move/framework/move-stdlib/sources/configs/features.move (L949-955)
```text
    /// When enabled, execution computes the trading-native state roots and commits them to
    /// `TransactionInfoV1`, so they are consensus-verified. Requires `TRANSACTION_INFO_V1`.
    /// Covers the native-position tree today and is intended to cover the other trading-native
    /// trees as they are added. Enabling it first commits the (empty-tree) roots to transaction
    /// info; the actual Move-side writes to those trees are gated by separate flags.
    /// Lifetime: permanent
    const COMPUTE_TRADING_NATIVE_STATE_ROOTS: u64 = 122;
```
