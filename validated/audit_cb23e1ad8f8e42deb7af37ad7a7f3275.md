## Finding

### Title
Replay/backup-verify accepts a divergent state root because `TransactionOutput::ensure_match_transaction_info` never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole authenticity check used by the transaction-replay verification path (`ChunkExecutorInner::verify_execution`, used by `db-tool replay-verify` and backup-cli's `TransactionRestoreBatchController` with `VerifyExecutionMode::Verify`). It checks status, gas, write-set hash, and event root, but a code comment in the function itself documents that it deliberately skips the state/hot-state/position-native checkpoint hashes bound into the trusted `TransactionInfo`. This mirrors the external bug's structure: two places that are supposed to compute/attest to the same committed value can silently diverge, and one caller trusts the divergent, unchecked result as fully verified.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  validates a re-executed `TransactionOutput` against the authenticated, accumulator-committed `TransactionInfo` for a version. It checks:
- transaction status [2](#0-1) 
- gas used [3](#0-2) 
- write-set hash vs `state_change_hash` [4](#0-3) 
- event root hash [5](#0-4) 

But it never compares the state checkpoint hash, hot-state checkpoint hash, or `position_state_checkpoint_hash` fields that are also part of `TransactionInfo` and are cryptographically bound into the ledger's accumulator/proof chain. The function's own comment documents this gap: [6](#0-5) 

This function is the *only* correctness check performed in the replay-verification code path `ChunkExecutorInner::verify_execution`, which re-executes transactions with the VM and calls `ensure_match_transaction_info` per-transaction — no state-tree root is recomputed or compared in this path (that only happens via `DoStateCheckpoint` in the normal `update_ledger` state-sync flow, which is *not* exercised here): [7](#0-6) 

This `verify_execution` path backs `TransactionReplayer`, which is used by backup/replay-verify tooling (`storage/backup/backup-cli/src/backup_types/transaction/restore.rs`, `storage/db-tool/src/replay_verify.rs`, `storage/backup/backup-cli/src/coordinators/replay_verify.rs`) with `VerifyExecutionMode::Verify`. In that mode, the write set is compared but the state (and, when `compute_trading_native_state_roots` is enabled, the native "position" state) root that would actually be committed to storage/accumulated by production nodes is never independently recomputed or checked in this specific check.

### Impact Explanation
For the replay/backup-verify flow that exists specifically to give integrity assurance that a backup restore or offline replay produces the exact same ledger state as the original, authenticated chain, a divergence in the JMT/state-checkpoint root (or the native-position state root used for trading/native-position state) between the locally recomputed value and the value implied by the trusted `TransactionInfo` will not be caught. `ensure_match_transaction_info` will report success as long as status, gas, write-set hash, and event root match, even though the actual committed state root differs. This can mask state-commitment bugs (e.g., in `DoStateCheckpoint::compute_position_checkpoint`, hot-state promotion logic, or any other state-root computation) that would otherwise corrupt durable ledger data or produce a wrong accumulator/state root being treated as verified — directly matching the "Committed state that differs from the correct VM result" and "Wrong ... state proof accepted as valid" impact categories.

### Likelihood Explanation
This is not a hypothetical: the gap is explicitly called out as a known, intentional limitation in the code itself, is on the only validation path used by production replay/backup-verify tooling, and requires no attacker action beyond a real divergence occurring in state-root computation elsewhere in the system (e.g. a bug in hot-state/position-state checkpoint computation, or the noted `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature). Given the tool is meant to be the last line of defense catching such divergences before they'd otherwise be caught by full replay/consensus, the likelihood of this gap masking a real integrity bug is non-trivial, though it is a "fail-open" verification bug rather than something an external attacker can directly weaponize on mainnet consensus itself.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when applicable) against values recomputed from the replayed execution output, or otherwise ensure `ChunkExecutorInner::verify_execution` invokes the same `DoStateCheckpoint` root-computation and known-hash comparison used in the primary state-sync `update_ledger` path before considering a chunk "verified."

### Proof of Concept
Not applicable as a runnable exploit — this is a verification-logic gap, not a state-corruption primitive by itself. It can be demonstrated by: (1) constructing a `TransactionOutput`/replay whose write set and events match a given `TransactionInfo` but whose state tree would produce a different `state_checkpoint_hash` (or `position_state_checkpoint_hash`), and (2) observing that `ensure_match_transaction_info` (as called from `ChunkExecutorInner::verify_execution`) returns `Ok(())` despite the mismatch, because no code path in `verify_execution` recomputes or checks those hash fields — confirmed by direct code inspection at [1](#0-0)  and [7](#0-6) .

*Note: I was unable to fully trace every downstream consumer of `VerifyExecutionMode::Verify` result (e.g., exact severity/user-facing consequence when `db-tool replay-verify` silently passes) within the available tool budget; a Devin session with full repo/terminal access could confirm end-to-end whether any other check compensates for this gap in the backup-verify CLI flow before treating this as fully confirmed for a bug bounty submission.*

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

**File:** execution/executor/src/chunk_executor/mod.rs (L685-708)
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
        Ok(end_version)
    }
```
