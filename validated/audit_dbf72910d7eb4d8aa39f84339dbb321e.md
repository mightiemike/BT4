### Title
`TransactionOutput::ensure_match_transaction_info` never validates state/hot-state/position checkpoint roots, letting replay-verify and chunk-executor re-execution accept a divergent state root as correct - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole integrity check used by two authenticated/trust-critical flows — `execution/executor/src/chunk_executor/mod.rs::verify_execution` and `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` — to confirm that locally re-executed transaction output matches the `TransactionInfo` recorded on the authenticated ledger (accumulator-proven) history. The function checks status, gas, write-set hash, and event root hash, but by its own admission never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. A divergence in any of those roots — the actual committed world-state root — is silently accepted as a match.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  compares only:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `CryptoHash::hash(self.write_set())` vs `txn_info.state_change_hash()`
- computed event root vs `txn_info.event_root_hash()`

It then returns `Ok(())` with an explicit TODO acknowledging the gap: [2](#0-1) 
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```

This function is the exact and only per-transaction correctness gate used by:
- `ChunkExecutorInner::verify_execution`, which re-executes a chunk of transactions and calls `txn_out.ensure_match_transaction_info(version, txn_info, Some(write_set), Some(events))` to confirm the locally computed output matches the backup/chunk manifest's authenticated `TransactionInfo` — [3](#0-2) 
- `db-tool`'s `replay_on_archive::Verifier::execute_and_verify`, which re-executes historical mainnet transactions and calls the same function to decide whether replay against archived data succeeded — [4](#0-3) 

Because `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` are never compared, if the locally computed post-execution state root (i.e., the Jellyfish Merkle root reflecting a checkpoint of the world state, hot-state, or the trading-native "position" state) diverges from the one embedded in the trusted `TransactionInfo`/accumulator-proof, while write-set hash, gas, status, and events happen to still match, the comparator reports success. Divergence in the checkpoint root without divergence in the write-set hash is possible because these roots are computed from state application semantics (e.g., merged with prior tree state, hot-state promotion rules, or the position/"trading-native" root, per `TransactionInfoV1`'s dedicated fields at [5](#0-4) ) that are not simply reducible to the write set's own hash — e.g., non-determinism, storage-schema/versioning bugs, or an unintended hard-fork-only code path affecting checkpoint construction would corrupt the root while leaving the write set encoding itself unaffected.

### Impact Explanation
This falls squarely under "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "committed state that differs from correct VM result" in the state-integrity gate. `replay_on_archive` and chunk-executor verification are the tools operators and auditors rely on to detect exactly this class of bug — a state root divergence introduced by an unintended change to checkpoint/state-root computation. Because the checkpoint hashes are excluded from the comparison, these tools will report a clean, successful replay/verification even when the actual committed state root differs from what local re-execution produces. This masks state-integrity corruption rather than causing it directly, but it removes the detection mechanism that would otherwise catch a wrong accumulator/state root before it propagated — directly undermining the "authenticated ... proof verification" invariant the task asks to protect. If `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`position_state_checkpoint_hash` verification is enabled expecting this comparator to guard it, silent tolerance of a wrong position-state root is a high-severity gap for the trading-native execution path.

### Likelihood Explanation
The gap is unconditional (not behind a feature flag) — anyone running `replay_on_archive` or chunk-executor's `verify_execution` today gets the reduced check regardless of `COMPUTE_TRADING_NATIVE_STATE_ROOTS`'s state, since the comment says the check should be added "before enabling" the feature but the code has not yet added it. It is self-documented in the code, confirming the root cause with certainty; the remaining uncertainty is only how easily an actual root-divergence-producing bug elsewhere in the codebase could occur, which this analysis does not need to demonstrate — the missing detection is itself the vulnerability.

### Recommendation
In `ensure_match_transaction_info`, add explicit comparisons between the locally computed checkpoint hashes (state / hot-state / position-state, when available on the `TransactionOutput`'s corresponding checkpoint output) and `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()` (on `TransactionInfoV1`), and `txn_info.position_state_checkpoint_hash()`, failing with a descriptive `ensure!` on mismatch, mirroring the pattern already used for write-set and event root hashes. This must land before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, as the code comment itself specifies.

### Proof of Concept
Local proof only (no live-network PoC possible from static analysis):
1. `ensure_match_transaction_info` source confirms it never reads `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` from either `self` (the freshly executed `TransactionOutput`) or `txn_info` (the trusted, accumulator-proven `TransactionInfo`): [6](#0-5) .
2. `verify_execution` in the chunk executor passes only `write_set` and `events` alongside the version to this function, so its trust decision for that transaction's correctness is fully governed by the four checked fields: [7](#0-6) .
3. `replay_on_archive::execute_and_verify` uses the identical call, treating an `Ok(())` result as "no verification error" for the entire replayed chunk range: [8](#0-7) .

Given these three code paths, a synthetic re-executed `TransactionOutput` whose write set hash, gas, status, and event root all match a target `TransactionInfo`, but whose state/hot-state/position checkpoint root does not, would pass `ensure_match_transaction_info` and be reported as a verified match by both the chunk executor and the `replay_on_archive` tool — demonstrating the exact corrupted-value class this comparator fails to catch.

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

**File:** types/src/transaction/mod.rs (L2440-2461)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct TransactionInfoV1 {
    gas_used: u64,
    status: ExecutionStatus,
    transaction_hash: HashValue,
    event_root_hash: HashValue,
    state_change_hash: HashValue,
    state_checkpoint_hash: Option<HashValue>,
    hot_state_checkpoint_hash: Option<HashValue>,
    auxiliary_info_hash: Option<HashValue>,

    /// Repurposed reserved field; `None` matches the prior BCS encoding.
    position_state_checkpoint_hash: Option<HashValue>,
    placeholder1: Option<HashValue>,
    placeholder2: Option<HashValue>,
    placeholder3: Option<HashValue>,
    placeholder4: Option<HashValue>,
    placeholder5: Option<HashValue>,
    placeholder6: Option<HashValue>,
    placeholder7: Option<HashValue>,
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
