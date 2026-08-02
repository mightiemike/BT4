## Title
Replay-verify skips checkpoint-hash validation, allowing corrupted committed state to pass integrity verification silently - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` — the routine used by the chunk-executor's replay-verify path (`execution/executor/src/chunk_executor/mod.rs::verify_execution`) and by the `aptos-debugger`/CLI replay tooling (`aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) to confirm that locally re-executed transaction outputs match the authenticated `TransactionInfo` on chain — only checks status, gas, write-set hash, and event-root hash. It explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, which is exactly the field that authenticates the resulting state (Merkle/JMT) root at that version.

### Finding Description [1](#0-0) 

```
pub fn ensure_match_transaction_info(...)
    ...
    let write_set_hash = CryptoHash::hash(self.write_set());
    ensure!(write_set_hash == txn_info.state_change_hash(), ...);
    ...
    let event_root_hash = ...;
    ensure!(event_root_hash == txn_info.event_root_hash(), ...);

    // TODO(trading-native): this comparator ignores the checkpoint hashes
    // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
    // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
    // replay even when the authenticated position state root diverges from
    // local execution. Validate the checkpoint hashes here before enabling
    // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
    Ok(())
```

This function is called from the epoch-boundary replay path in the chunk executor: [2](#0-1) 

and from the debugger's mismatch printer used by replay/verify tooling: [3](#0-2) 

By contrast, the normal state-sync commit path (`StateSyncChunkVerifier::verify_chunk_result`) computes a full state-checkpoint via `DoStateCheckpoint` and compares the *entire* `TransactionInfo` set (including checkpoint hashes) via `ensure_transaction_infos_match`, so live consensus/state-sync commit is not directly affected by this gap. [4](#0-3) 

However, the replay/verify code path — which is the tool operators and auditors rely on to confirm that a committed chain segment (from backups or archives) is a correct product of the VM — only asserts write-set hash and event-root hash equality. It skips the `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` comparison, which is precisely the field that binds a `TransactionInfo` (and thus the accumulator leaf and the accumulator root/ledger info) to a specific *state tree* result, as opposed to merely the write set applied in that one transaction. Two divergent state trees (e.g., due to a bug in JMT construction, hot-state computation, or the new "trading native" state root computation gated by `compute_trading_native_state_roots`) can produce identical write sets/events for a transaction while producing a different resulting checkpoint hash; this tool will not detect the divergence.

### Impact Explanation
This breaks the "replay/restore must preserve deterministic proof binding" invariant called out in the scan brief. Replay-verify (`db-tool`'s `replay-verify`/`replay_on_archive`, and `aptos-debugger`) is the primary mechanism used to independently confirm that historically committed `TransactionInfo`s (whose hashes are covered by validator signatures via the accumulator root in `LedgerInfo`) are the correct result of VM execution. If the state/hot-state/position checkpoint hash silently diverges between the authenticated ledger and a fresh execution, this tool reports success anyway, masking:
- A state-commit bug that already caused wrong state to be accepted on mainnet (hard-fork-class divergence not caught by the auditing tool designed to catch exactly this), and
- Specifically for the new "trading native" / position-state feature (`compute_trading_native_state_roots`), any bug in position-state root computation would go undetected by replay-verify precisely because this check is the gate the TODO says must be added "before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS."

This is an authenticated-response/proof-verification-adjacent tool blind spot rather than a live consensus-safety bypass (I could not show that this same short-circuited comparison is used in the live block-commit/state-sync path — that path uses `ensure_transaction_infos_match` doing full `TransactionInfo` equality, which does include checkpoint hashes). The vulnerability is confined to the replay/verify auditing tooling and to the epoch-boundary "replay" chunk-verifier used during backup restore.

### Likelihood Explanation
The gap is unconditional in the current code (no feature flag guards it) and is explicitly documented as a known, intentionally deferred hole by the surrounding TODO comment, which states this must be fixed before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled. Since the feature flag and associated call sites (`compute_trading_native_state_roots` threaded through `do_get_execution_output.rs`, `do_state_checkpoint.rs`, chunk executor) already exist in the codebase, the precondition for the gap to matter (a real divergence in state/hot-state/position root computation slipping through write-set/event equality) is realistically approaching activation.

### Recommendation
In `TransactionOutput::ensure_match_transaction_info`, add comparisons between the locally recomputed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when computed/available) and the corresponding fields on `txn_info`, mirroring the full-equality check already done in `LedgerUpdateOutput::ensure_transaction_infos_match` used by the state-sync commit path. This should be enforced (or plumbed through with an explicit "known-checkpoint" parameter passed from `verify_execution`) prior to enabling `compute_trading_native_state_roots` on mainnet.

### Proof of Concept
Not directly exploitable as a standalone PoC without a concrete state-root divergence bug to trigger, since the finding is a missing-check in an auditing tool rather than a state-mutation primitive by itself. Conceptually:
1. Introduce (or have present) any divergence between the authenticated on-chain `state_checkpoint_hash`/`position_state_checkpoint_hash` for a given version and what a node independently computes for the same write set/events (e.g. a bug in JMT/hot-state root aggregation, or a bug specific to position-state computation).
2. Run `db-tool replay-verify` (or `aptos-debugger`) over that version range.
3. `verify_execution` → `ensure_match_transaction_info` compares only write-set hash, event-root hash, gas, and status; since these match, the tool reports success even though the state roots differ, hiding the underlying divergence. [5](#0-4)

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
