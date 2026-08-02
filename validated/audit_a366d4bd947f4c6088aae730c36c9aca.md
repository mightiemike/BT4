Confirmed: `LedgerUpdateOutput::ensure_transaction_infos_match` (execution/executor-types/src/ledger_update_output.rs:92-114) compares full `TransactionInfo` structs (`txn_info == expected_txn_info`), so the state-sync/consensus commit path (`StateSyncChunkVerifier::verify_chunk_result`) does catch any divergence in `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`. The gap is isolated to `TransactionOutput::ensure_match_transaction_info`, which is the comparator used by the chunk-executor's own `verify_execution` (replay-verify) path and by offline tooling (`aptos-debugger`, `aptos-move/cli`, `db-tool/replay_on_archive`).

### Title
Replay-verify comparator silently ignores state/hot-state/position checkpoint hashes, letting divergent authenticated state roots pass verification — (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole invariant check used by chunk-executor replay-verification and by db-tool/CLI/debugger replay flows to confirm that a locally re-executed transaction produced the same result as the one committed and proven on-chain. It checks status, gas, write-set hash, and event-root hash, but explicitly skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the fields that authenticate the Sparse-Merkle/Jellyfish state root bound to a `TransactionInfo`. This is the same class of bug as the source report: a value that should be reconciled against the actual outcome (the real computed root) is instead left unchecked/stale, so a persisted "verification" signal (successful replay) is produced even when the actual state diverges from the authenticated value.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  validates status, gas_used, `state_change_hash` (write-set hash), and `event_root_hash`, but its trailing comment explicitly documents that it does not compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the locally computed values [2](#0-1) .

This comparator is the integrity gate used in three places that are meant to detect state divergence during replay:
- Chunk-executor internal execution verification: `verify_execution` calls it per-transaction against externally supplied `write_sets`/`events`/`transaction_infos` after re-executing the chunk locally [3](#0-2) .
- `aptos-debugger`'s mismatch printer/system-transaction replay path [4](#0-3) .
- The `aptos` CLI transaction-replay command, used to validate a fetched historical transaction against the chain's authenticated `TransactionInfo` [5](#0-4) .

By contrast, the safe path is `LedgerUpdateOutput::ensure_transaction_infos_match`, used only by the state-sync/consensus commit verifier (`StateSyncChunkVerifier::verify_chunk_result`), which compares whole `TransactionInfo` structs (including all checkpoint-hash fields) and therefore would catch such a divergence [6](#0-5) [7](#0-6) . So the normal block-commit path is not affected — the gap is specifically in the tooling/replayer path that operators, auditors, and `db-tool replay_on_archive` rely on to independently confirm history integrity, particularly for `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/position-state and hot-state features whose checkpoint roots are only asserted via `state_checkpoint_hash`-family fields [8](#0-7) .

### Impact Explanation
If a bug in the position-state/hot-state checkpoint computation (`DoStateCheckpoint::compute_position_checkpoint`, hot-state accumulation, etc.) causes a locally computed state root to diverge from the authenticated one recorded in the historical `TransactionInfo`, none of the three tooling entry points above would detect it: `ensure_match_transaction_info` returns `Ok(())` regardless of a checkpoint-hash mismatch. This means:
- Chunk-executor's own "verify execution" replay mode (`VerifyExecutionMode`) can report a chunk as successfully re-verified even though its state/hot-state/position root diverges from the chain's authenticated value.
- `db-tool`'s `replay_on_archive`, used to validate archived history and detect state-corrupting bugs before they are trusted (e.g., to sign off on a bootstrap snapshot or investigate a suspected divergence), can give a false "PASS."
- Downstream consumers of these tools (auditors, node operators bootstrapping from a snapshot, incident responders) get a false assurance that committed history/state is correct.

This is bounded: it is a proof-verification blind spot in replay/tooling paths, not a way to get bad state committed by consensus/state-sync itself (that path independently re-checks full `TransactionInfo` equality). The impact is scoped to state-integrity verification tooling failing to flag a real divergence — which matches the "proof/verification accepted as valid despite being wrong" pattern called out in the assignment, but its blast radius is limited to detection/tooling rather than live consensus safety.

### Likelihood Explanation
The gap is unconditionally present in code today (not behind a feature flag) — any bug or edge case that produces an incorrect `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` during re-execution will not be caught by any of the three call sites. Note the code itself contains a `TODO(trading-native)` comment acknowledging exactly this gap and stating it should be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" [2](#0-1) ; this indicates the maintainers are aware but it has not yet been remediated in this codebase snapshot, which lowers novelty but the exposure remains live in the current code.

### Recommendation
Extend `ensure_match_transaction_info` to compute the locally derived `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` (where computable in the given replay context) and assert equality with `txn_info`'s corresponding fields, mirroring what `LedgerUpdateOutput::ensure_transaction_infos_match` already does for the consensus/state-sync path. At minimum, gate any enablement of `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (and hot-state-root-in-txn-info) on this check being implemented, per the existing TODO.

### Proof of Concept
Conceptual: 
1. Introduce (or trigger via an existing bug) a divergence between the locally recomputed position-state checkpoint root and the historically committed `position_state_checkpoint_hash` in a `TransactionInfoV1`.
2. Run chunk-executor's replay-verify (`enqueue_chunks` + `verify_execution`) over that version range, or run `db-tool replay_on_archive` / the CLI `replay-transaction`-style command against that version.
3. Observe that `ensure_match_transaction_info` returns `Ok(())` because it only checks status/gas/write-set hash/event-root hash [1](#0-0) , so the tool reports a successful, matching replay despite the state root actually differing — a proof/verification-integrity failure in the replay tooling.

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

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L233-245)
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
```

**File:** aptos-move/cli/src/commands.rs (L2797-2813)
```rust
        // Materialize into transaction output and check if the outputs match.
        let txn_output = vm_output.into_transaction_output().map_err(|err| {
            CliError::UnexpectedError(format!(
                "Failed to materialize into transaction output: {}",
                err
            ))
        })?;

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L44-49)
```rust
        let state_checkpoint_hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_state_checkpoints,
            last_checkpoint.root_hash(),
            "state",
        )?;
```
