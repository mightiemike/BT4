## Title
`ensure_match_transaction_info` never validates `state_checkpoint_hash` during chunk-executor replay verification, allowing a divergent SMT root to pass state-sync/backup-replay integrity checks - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function that binds freshly-computed VM output back to a previously committed, accumulator-authenticated `TransactionInfo` during chunk replay (state sync verify-execution and backup restore/replay-verify tooling). It checks `status`, `gas_used`, `state_change_hash` (write-set hash), and `event_root_hash`, but explicitly does **not** check `state_checkpoint_hash` (nor `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`).

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates only a subset of the fields inside `TransactionInfo`. The function's own comment acknowledges the gap: [2](#0-1) 

`state_checkpoint_hash` is the periodic (per-checkpoint/per-block) root hash of the *entire* Sparse Merkle Tree state, not just this transaction's own write set. It is the authenticated commitment that ties the ledger's global state to the accumulator-signed `TransactionInfo`. Because this field is skipped, a chunk of transaction outputs whose per-transaction `write_set` hash and `event_root_hash` are correct, but whose *cumulative* effect on global state diverges from what the original, signed `TransactionInfo.state_checkpoint_hash` records (e.g., due to a bug or corruption in a prior transaction's application, or a subtly different execution order/parent-state), will still pass `ensure_match_transaction_info` successfully.

This function is used by `ChunkExecutorInner::verify_execution` in the state-sync/backup replay path: [3](#0-2) , as well as by `db-tool`'s `replay_on_archive` and the `aptos-debugger`/CLI replay tooling. In all these call sites it is the sole per-transaction correctness gate deciding whether replayed VM output is accepted as matching the archived, proof-bound ledger state.

### Impact Explanation
This is a genuine state-commitment integrity gap: the authenticated global state root (`state_checkpoint_hash`), which is exactly the kind of "wrong ... state proof accepted as valid" scenario called out in the state-integrity gate, is silently excluded from the comparison. In state-sync/backup-replay flows this means a wrong world-state root can be treated as verified/matching even though it does not correspond to the correct VM result, undermining the guarantee that "committed state differs from the correct VM result" is detected during replay/restore. The severity is tempered because:
- `state_change_hash` (the write-set hash) is still checked, which catches many but not all divergence scenarios (specifically ones that stem from applying a correct write-set onto an already-diverged base state).
- The gap is explicitly flagged in-code as a known TODO tied to enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, a currently-disabled feature flag (`types/src/on_chain_config/aptos_features.rs`), suggesting the authors are aware and treat it as not-yet-critical while that feature is off.
- The affected call sites (`chunk_executor::verify_execution`, `replay_on_archive`, `aptos-debugger`) are verification/replay/backup tooling paths, not the live consensus-execution commit path itself, so it does not directly let a validator commit wrong state to the live ledger through normal consensus, but it does weaken the trust of backup-restore and archive-replay verification, which are the exact "restore flows" and "replay" paths called out as in-scope.

### Likelihood Explanation
Exploiting this requires a scenario where a prior state divergence already exists (e.g., through a separate bug, non-deterministic execution, or malicious backup data) that changes the cumulative state root while leaving each individual write set's hash unchanged relative to the corresponding `TransactionInfo`. This is a narrower, second-order condition rather than a single trivially triggerable input, and largely depends on other invariants holding elsewhere (e.g., accumulator proof, `state_change_hash` checks). Given it is an acknowledged, feature-gated gap in tooling rather than the primary consensus commit path, likelihood on mainnet today is low-to-moderate.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `expected` vs. actual `state_checkpoint_hash` (and, once relevant, `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) whenever the checkpoint hash is `Some` in the reference `TransactionInfo`, rather than deferring this validation to a future feature flag. This closes the gap for all current callers (`chunk_executor::verify_execution`, `replay_on_archive`, CLI replay/debugger tools) instead of only guarding it behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

### Proof of Concept
Conceptual PoC (cannot be executed without full DB/backup infrastructure available only in a live session):
1. Construct a backup/replay chunk where, due to any prior discrepancy (corrupted intermediate transaction, or an executor bug applying writes to a stale/incorrect base state), the resulting Sparse Merkle Tree root after some transaction differs from the `state_checkpoint_hash` recorded in the archived, ledger-info-signed `TransactionInfo`, while that transaction's own `write_set` bytes (and thus `state_change_hash`) and `events` still match.
2. Feed this chunk through `ChunkExecutorInner::verify_execution` (`execution/executor/src/chunk_executor/mod.rs:685-706`) or `db-tool`'s `replay_on_archive`.
3. Observe that `ensure_match_transaction_info` returns `Ok(())` despite the divergent state root, because `state_checkpoint_hash` is never compared — the replay/restore/verify pipeline reports success even though the reconstructed global state does not match the authenticated ledger state.

Note: I was not able to fully trace whether any other check upstream of `ensure_match_transaction_info` (e.g., accumulator proof verification against the ledger info in `TransactionInfoListWithProof::verify`) independently catches this exact divergence in all call paths; a Devin session with full build/test access would be needed to construct and run a concrete reproduction to confirm end-to-end exploitability versus being caught by another layer.

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
