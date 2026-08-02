## Confirmed local root cause

This confirms a real, code-proven gap: the normal state-sync/replay commit path (`StateSyncChunkVerifier` and `ReplayChunkVerifier` in `chunk_result_verifier.rs`) validates the **full** `TransactionInfo` equality via `LedgerUpdateOutput::ensure_transaction_infos_match` [1](#0-0) , which compares the complete `TransactionInfo` struct — including `state_checkpoint_hash` (the Merkle state root). But the separate `VerifyExecutionMode`/`verify_execution` path used by the chunk executor for lazy re-verification, and by `replay_on_archive`/`aptos move replay`, calls `TransactionOutput::ensure_match_transaction_info` instead [2](#0-1) , and that function explicitly and only checks status, gas, write-set hash, and event root hash — never the state/hot-state/position checkpoint hashes, as its own comment admits [3](#0-2) .

### Title
Replay-verification (`ensure_match_transaction_info`) never validates the state-checkpoint root, letting a corrupted state root pass as "verified" - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-equivalence check used by chunk-executor replay verification (`VerifyExecutionMode::should_verify()`), by `db-tool`'s `replay_on_archive` verify path, and by the Move CLI/debugger replay tool to assert that local re-execution reproduces the on-chain-committed result. It checks transaction status, gas used, write-set hash (`state_change_hash`), and event root hash, but it never checks `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` — i.e. it never confirms that applying the write set to the JMT/state tree actually produces the correct, previously-committed state root.

### Finding Description
`ensure_match_transaction_info` performs four `ensure!` checks (status, gas, write-set hash, event root hash) and then returns `Ok(())` with a code comment acknowledging the gap [4](#0-3) .

This function is invoked from three independent tools that claim to "verify" execution against ledger history:
- `execution/executor/src/chunk_executor/mod.rs::verify_execution`, used whenever `VerifyExecutionMode::should_verify()` is set during chunk replay [5](#0-4) .
- `storage/db-tool/src/replay_on_archive.rs`, the standalone mainnet-archive replay-verification tool.
- `aptos-move/cli/src/commands.rs`, the `aptos move replay` debugging command [6](#0-5) .

By contrast, the *primary* commit-time chunk verification path (`StateSyncChunkVerifier`/`ReplayChunkVerifier`) uses `LedgerUpdateOutput::ensure_transaction_infos_match`, which compares the whole `TransactionInfo` object (derived from the freshly executed accumulator leaves) field-for-field, including `state_checkpoint_hash` [1](#0-0) . So the state root **is** cryptographically bound during normal state-sync chunk application. The gap is isolated to the secondary/"extra verification" tools that call `ensure_match_transaction_info` directly on a `TransactionOutput`, bypassing the accumulator/`TransactionInfo` construction path entirely.

The practical effect: if a bug elsewhere in write-set application, JMT node construction, or state-checkpoint hashing (e.g. `execution/executor/src/workflow/do_state_checkpoint.rs`) causes the locally-computed state root to diverge from the historically-committed root — while the write set itself, gas, status, and events remain byte-identical — none of the three verification tools above will detect it. They will all report the replay as successfully matching, even though the authenticated state-commitment root has silently diverged.

### Impact Explanation
This directly matches the "Proof And Storage Pivots" criteria: an authenticated commitment (the state-checkpoint root inside `TransactionInfo`, which is itself hashed into the transaction accumulator and thus the `LedgerInfo`) is not re-derived and compared during the verification tools whose entire purpose is to catch such divergence. On mainnet this specific gap surfaces in `replay_on_archive` (used to audit historical mainnet execution for correctness/hard-fork detection) and in `VerifyExecutionMode` chunk-executor replay (used during fast-sync/backup-restore verification with re-execution). A latent state-root-affecting bug (e.g., in hot-state or JMT commit logic) could silently corrupt durable state while every available "verify execution" tool reports success, defeating the primary safety net for detecting non-deterministic or buggy state-commitment logic before/after a hard fork.

### Likelihood Explanation
The gap itself is deterministic and always present (not a race or timing issue) whenever these code paths are exercised — the comparison is simply missing, as explicitly acknowledged by the surrounding TODO comment. What is *not* verified locally (and would require live experimentation to fully confirm) is whether any of the other layers, such as `LedgerUpdateOutput::ensure_transaction_infos_match` used elsewhere, might independently catch the same defect in the paths reachable from `replay_on_archive`/`verify_execution` at a higher layer before/after this call. From the code read, `verify_execution` in the chunk executor calls only `ensure_match_transaction_info`, with no follow-up state-root check for that batch.

### Recommendation
Extend `ensure_match_transaction_info` (or add a companion check before enabling any features that rely on it, as the existing TODO already flags) to recompute the state-checkpoint hash (and, if applicable, hot-state/position-state checkpoint hashes) from the resulting state and compare it against `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()`, failing loudly on mismatch, consistent with what `ensure_transaction_infos_match` already does for the primary chunk-executor path.

### Proof of Concept
Not independently exploitable as a standalone PoC without inducing a genuine state-root-corrupting bug elsewhere; the finding is a verification-gap (missing invariant check), demonstrated by direct code reading:
1. `ensure_match_transaction_info` body only performs 4 `ensure!` checks (status/gas/write-set-hash/event-root-hash) and returns `Ok(())` — [4](#0-3) .
2. It is the sole verification call in `verify_execution` for chunk-executor replay-verify mode — [2](#0-1) .
3. Contrast with `ensure_transaction_infos_match`, which does compare full `TransactionInfo` (state-root inclusive) and is used only in the non-`VerifyExecutionMode` chunk-application path — [1](#0-0) .

### Citations

**File:** execution/executor-types/src/ledger_update_output.rs (L92-114)
```rust
    pub fn ensure_transaction_infos_match(
        &self,
        transaction_infos: &[TransactionInfo],
    ) -> Result<()> {
        ensure!(
            self.transaction_infos.len() == transaction_infos.len(),
            "Lengths don't match. {} vs {}",
            self.transaction_infos.len(),
            transaction_infos.len(),
        );

        let mut version = self.first_version();
        for (txn_info, expected_txn_info) in
            zip_eq(self.transaction_infos.iter(), transaction_infos.iter())
        {
            ensure!(
                txn_info == expected_txn_info,
                "Transaction infos don't match. version:{version}, txn_info:{txn_info}, expected_txn_info:{expected_txn_info}",
            );
            version += 1;
        }
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

**File:** aptos-move/cli/src/commands.rs (L2651-2655)
```rust
                if !self.skip_comparison {
                    txn_output
                        .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                        .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
                }
```
