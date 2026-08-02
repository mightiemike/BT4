Based on my investigation, I found a concrete, locally-provable integrity gap in Aptos's replay-verification path, distinct from the Ajna dust-threshold bug but analogous in structure: a validation routine that is supposed to catch *all* divergences between locally-computed VM output and previously-committed/authenticated data instead only checks a subset of fields, silently accepting mismatches in others.

### Title
Replay-verify's `ensure_match_transaction_info` skips checkpoint-hash fields, allowing divergent state roots to pass verification - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole correctness gate used by the `db-tool replay-on-archive` tool to confirm that locally re-executed transactions match the authenticated, previously-committed `TransactionInfo`. The function checks status, gas used, write-set hash, and event root hash, but a `TODO` comment in the code itself documents that it deliberately skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`.

### Finding Description
`ensure_match_transaction_info` computes and compares `status`, `gas_used`, the write-set hash against `state_change_hash`, and the event-root hash, then returns `Ok(())` without ever comparing the checkpoint-hash fields carried in `TransactionInfoV1` [1](#0-0) . The trailing comment explicitly states this ignores the state/hot-state checkpoint hashes and `position_state_checkpoint_hash`, and that "replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution" [2](#0-1) .

This function is invoked as the only per-transaction correctness check in `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which drives the archive replay-verification workflow: it re-executes a chunk of transactions, then calls `ensure_match_transaction_info` on each output against the archived `expected_txn_infos`, treating a returned `Ok(())` as proof the replay matches the authenticated ledger [3](#0-2) .

`TransactionInfoV1` carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and the repurposed `position_state_checkpoint_hash` as authenticated, accumulator-committed fields [4](#0-3) , and these roots are produced by `DoStateCheckpoint`/state-checkpoint logic tied to the `compute_trading_native_state_roots` on-chain config during normal execution [5](#0-4) . Because the verifier used by the replay tool never compares these fields, a local re-execution that produces a *different* state/hot-state/position-state root than what was actually committed and cryptographically bound into the transaction accumulator would still be reported as a successful, matching replay.

### Impact Explanation
This breaks the proof/commit-integrity invariant that replay-verify and archive-replay tooling are meant to enforce: that locally recomputed VM state matches the authenticated on-chain state root. If the state-checkpoint or position-state-checkpoint computation diverges (e.g. due to a bug in state-checkpoint construction, a non-determinism in the "trading-native" state roots, or a malicious/corrupted archive dataset), the replay-verify tool would not detect it and would falsely certify the archived history as consistent. This is exactly the class of "hard-fork-only divergence during commit, replay, restore, or proof verification" that the gate calls out, since divergence in this checked value would only surface via other means (or not at all) rather than through the dedicated verification tool.

### Likelihood Explanation
The gap is unconditional in the current code (no feature gate on the check itself — it is simply omitted), so it triggers whenever `TransactionInfoV1`/checkpoint hashes are used and a discrepancy occurs. Its practical exposure currently depends on `compute_trading_native_state_roots` and hot-state checkpoint features being active, but the check omission is a root-cause code defect and the comment shows the authors are already aware of it as an open, unresolved TODO.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in `txn_info`) against the actual computed checkpoint roots before returning `Ok(())`, so `replay_on_archive` and any other caller cannot report success while these authenticated roots diverge.

### Proof of Concept
1. Run `db-tool replay-on-archive` against a chunk whose transactions include a state checkpoint (block epilogue) with `compute_trading_native_state_roots`/hot-state enabled.
2. Locally re-execute with a state-checkpoint implementation that computes a different `position_state_checkpoint_hash`/`hot_state_checkpoint_hash` than the archived, authenticated value (e.g., due to a bug or a tampered archive).
3. `execute_and_verify` calls `ensure_match_transaction_info`, which only checks status/gas/write-set hash/event-root hash [6](#0-5) ; since these all still match, the function returns `Ok(())` and the tool records no failure, despite the state/position root mismatch.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L392-406)
```rust
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

**File:** execution/executor/src/workflow/do_get_execution_output.rs (L387-413)
```rust



























```
