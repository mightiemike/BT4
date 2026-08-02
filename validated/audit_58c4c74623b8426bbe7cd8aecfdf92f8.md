## Finding

### Title
`TransactionOutput::ensure_match_transaction_info` skips checkpoint-hash verification, allowing replay/verify and state-sync execution-verification paths to accept a state root that diverges from local execution - ([File: types/src/transaction/mod.rs])

### Summary
The Linea report's root cause is that a commitment submitted by an untrusted party (`submitData`) is never checked against the "ground truth" value computed independently (the prover's `y`), so a divergence is only caught later, if at all. The Aptos analog is `TransactionOutput::ensure_match_transaction_info`, the function used across the codebase to check that locally re-executed results match the persisted/canonical `TransactionInfo` (the analog of the "prover" commitment). This function verifies status, gas, write-set hash, and event-root hash, but explicitly does **not** verify the state-checkpoint hash, hot-state-checkpoint hash, or `position_state_checkpoint_hash` fields that are also carried in `TransactionInfo` and bound into the transaction-accumulator leaf hash.

### Finding Description
`ensure_match_transaction_info` is documented in-code as intentionally incomplete: [1](#0-0) 

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```

This comparator is the single verification chokepoint that several independent consumers rely on to determine whether locally-computed execution output matches an externally supplied `TransactionInfo`/write-set/events tuple:

- `execution/executor/src/chunk_executor/mod.rs`'s `verify_execution`, used by state-sync chunk execution-verification mode, calls `txn_out.ensure_match_transaction_info(...)` per transaction to decide whether locally re-executed output matches the transaction infos supplied by the chunk. [2](#0-1) 
- `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify` calls the same comparator to decide whether replayed transactions match the expected (previously-committed) `TransactionInfo`s, write sets, and events read from an archive. [3](#0-2) 
- `aptos-move/cli/src/commands.rs` also calls it.

Because the comparator never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, none of these three call sites can detect a divergence between the locally re-computed state-tree root (produced by `DoStateCheckpoint`/`DoLedgerUpdate`, see `execution/executor/src/workflow/do_state_checkpoint.rs` and `do_ledger_update.rs`) and the state root actually recorded in the `TransactionInfo` that was accepted into the transaction accumulator and signed by validators. In other words, the accumulator leaf (and therefore the `LedgerInfo` root hash) can encode one state root while the verification tooling — which is supposed to be the authoritative check that "the data submitted matches the data computed" — silently reports success.

This mirrors exactly the structure of the Linea finding: a commitment (`state_checkpoint_hash`/`position_state_checkpoint_hash`) is produced and stored, a supposedly-authoritative re-computation exists, but the code that is meant to compare the two commitments omits the check for a subset of the fields, so a mismatch on those fields goes undetected until — if ever — something else (e.g., full JMT reconstruction, `check_txn_info_hashes` debug tool) happens to catch it.

### Impact Explanation
- For state-sync `verify_execution` mode, a corrupted or maliciously-crafted `position_state_checkpoint_hash`/`state_checkpoint_hash` in a supplied chunk's `TransactionInfo` will not be flagged, even though this hash is part of the accumulator leaf hash that must match the target `LedgerInfo`'s `transaction_accumulator_hash`. This weakens exactly the invariant that "committed state that differs from the correct VM result" must be detected. [4](#0-3) 
- For `replay_on_archive`/CLI replay-verify tooling, a divergence in the authenticated position-state (or hot-state) root between what was originally computed on-chain and what current node logic recomputes will be silently missed, undermining the primary safety net used to detect state-divergence bugs before/after a hard fork.

The comment itself flags the concrete risk: enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` without first closing this gap means the authenticated position-state root can diverge from local execution and go undetected by exactly the tooling built to catch that divergence.

### Likelihood Explanation
The code path is unconditionally reachable today whenever `ensure_match_transaction_info` is invoked (it is not itself gated behind a feature flag), and the feature flag `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (`types/src/on_chain_config/aptos_features.rs`) is the trigger condition explicitly called out by the code comment as unsafe to enable while this gap exists. The gap is self-documented as a known, unresolved TODO in the shipped code, indicating the maintainers are aware but have not yet implemented the fix.

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in `txn_info`) against the checkpoint hashes computed locally for that transaction, consistent with how `write_set_hash` and `event_root_hash` are already checked. This must be completed before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled on any network, as called out by the existing TODO.

### Proof of Concept
1. Produce a `TransactionInfo` (V1) whose `position_state_checkpoint_hash` (or `state_checkpoint_hash`) does not match the actual position-state/state root that would be computed by re-executing the corresponding transaction (e.g., by tampering with an archived transaction info, or by a state-sync peer sending a chunk with a mismatched checkpoint hash but otherwise-consistent status/gas/write-set/events).
2. Feed this into `chunk_executor::verify_execution` (state-sync execution-verification mode) or `replay_on_archive::execute_and_verify`.
3. Observe that `ensure_match_transaction_info` returns `Ok(())` despite the checkpoint hash mismatch, because it never compares those fields — the divergence in authenticated state root is not detected by the intended verification step. [5](#0-4)

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

**File:** execution/executor/src/chunk_executor/mod.rs (L648-658)
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
