## Title
Replay-verify and chunk `verify_execution` accept a wrong committed state root because `ensure_match_transaction_info` never checks `state_checkpoint_hash` - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single comparator used by both the state-sync chunk executor's execution-verification path and the `replay_on_archive` replay-verify tool to confirm that a locally re-executed transaction matches the authoritative, previously-committed `TransactionInfo`. The function checks status, gas used, write-set hash, and event root hash, but it never checks `state_checkpoint_hash` (nor `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`). This means any divergence in the cumulative Sparse/Jellyfish Merkle state root - the actual committed ledger state - passes verification silently as long as the per-transaction write set happens to hash the same.

### Finding Description
`ensure_match_transaction_info` performs four checks and returns `Ok(())` unconditionally after that, explicitly skipping the checkpoint hashes: [1](#0-0) 

The function is reused, unmodified, by two integrity-critical call sites:

1. `execution/executor/src/chunk_executor/mod.rs::verify_execution`, which re-executes a chunk locally and compares the result against expected `TransactionInfo`s pulled from backup/state-sync data: [2](#0-1) 

2. `storage/db-tool/src/replay_on_archive.rs::execute_and_verify`, the dedicated replay-verify tool whose entire purpose is to detect execution/state divergence (e.g. hard-fork-only bugs) between local re-execution and the archived, authoritative ledger: [3](#0-2) 

`state_checkpoint_hash` is the root of the entire accumulated world state (Sparse/Jellyfish Merkle Tree), not just the current transaction's write set. It is possible for a transaction's own `write_set_hash` and `event_root_hash` to match while the *overall* state root still diverges, because the checkpoint hash depends on the state produced by all prior transactions plus the current one being folded into the tree/hot-state/position-state structures. A bug in state-checkpoint construction (e.g. in `DoStateCheckpoint`, hot-state root computation, or `position_state_checkpoint_hash` for trading-native state, all referenced in the TODO comment right below the check) would therefore go completely undetected by either verification path, even though these paths exist specifically to catch such divergence.

### Impact Explanation
This breaks the state-commitment integrity invariant that "committed state that differs from the correct VM result... must not be accepted as valid" and that "replay paths... must not reinterpret committed data into a different ledger state." Both the state-sync chunk-executor's execution-verification mode and the `db-tool replay_on_archive` tool - the two mechanisms explicitly designed to catch state-root divergence across a chunk/segment of history - will report success (`Ok(())`) even when the locally-computed state root disagrees with the persisted/authoritative one. A hard-fork-class bug in checkpoint/state-root computation (SMT, hot-state, or position-state root) would silently propagate through fast-sync verification and through replay-verify audits used to certify archive/backup correctness, undermining confidence in ledger data that operators believe has been independently re-verified.

### Likelihood Explanation
This is not attacker-triggered; it requires an underlying bug in state-checkpoint/root computation to exist and manifest during a chunk replay, backup restore verification, or replay-verify run. Given the comment explicitly calls this out as a known gap tied to the newer `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature (hot-state / position-state roots), and given `state_checkpoint_hash` itself (the base SMT root, unrelated to trading-native) is also unconditionally skipped, any regression in these state-root computation paths has a direct route to passing as "verified" during exactly the tooling meant to catch it.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`'s locally-computed state checkpoint hash(es) against `txn_info.state_checkpoint_hash()` (and, where applicable, `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`) whenever the transaction is expected to carry a checkpoint (i.e., `Some(...)`), returning an error on mismatch just as is done for `write_set_hash` and `event_root_hash`. This requires threading the locally-computed checkpoint root(s) into the callers (`chunk_executor::verify_execution` and `replay_on_archive::execute_and_verify`), since `TransactionOutput` alone does not carry it.

### Proof of Concept
1. Introduce (or trigger via a real bug) a divergence purely in state-checkpoint root computation (e.g., in `DoStateCheckpoint`/hot-state root logic) that does not change the per-transaction write set, event set, gas, or status for the affected transaction(s).
2. Run `db-tool replay_on_archive` (or trigger chunk-executor `verify_execution`) over the affected version range.
3. Observe that `execute_and_verify`/`verify_execution` return success for every transaction because `ensure_match_transaction_info` only checks status/gas/write_set_hash/event_root_hash, never state_checkpoint_hash, despite the root diverging from the authoritative `TransactionInfo` fetched from backup/persisted data.

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
