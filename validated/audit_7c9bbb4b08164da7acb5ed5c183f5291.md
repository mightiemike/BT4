I have enough to write up the finding now.

### Title
`ensure_match_transaction_info` skips checkpoint-hash verification, allowing corrupted state/position roots to pass restore, replay-verify, and chunk-execution validation - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single authenticated invariant used across restore, replay-verify tooling, and `ChunkExecutor::verify_execution` to confirm that a locally re-executed `TransactionOutput` matches the trusted, ledger-info-proven `TransactionInfo` for a given version. The function checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but the code itself documents, via an unresolved `TODO`, that it never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This means a locally computed state/position Merkle root can silently diverge from the value embedded in (and covered by) the authenticated `TransactionInfo`/accumulator, while every consumer of `ensure_match_transaction_info` reports success. [1](#0-0) 

### Finding Description
`TransactionInfoV0`/`TransactionInfoV1` carry `state_checkpoint_hash` (and, for V1, `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`) as part of the data that is hashed into the transaction accumulator and therefore authenticated by ledger-info signatures. [2](#0-1) 

`ensure_match_transaction_info` is meant to be the complete cross-check between a locally produced `TransactionOutput` and this authenticated `TransactionInfo`. It validates `status`, `gas_used`, the write-set hash against `state_change_hash`, and the event root hash — but explicitly does *not* validate any of the checkpoint hashes, as stated in its own trailing comment:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [3](#0-2) 

This function is invoked from multiple integrity-sensitive paths:
- `ChunkExecutor::verify_execution`, which re-executes a chunk of transactions and checks that the *authenticated* target result matches local re-execution before treating a chunk-executed/output-applied sync as verified. [4](#0-3) 
- `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify`, the tool operators run to confirm that replaying archived transactions against a local state produces the exact ledger recorded on mainnet. [5](#0-4) 
- `aptos-move/aptos-debugger` and `aptos-move/cli` for one-off replay/debug verification.

Because the checkpoint hashes are excluded from the comparison, a divergence in the locally computed state root (`state_checkpoint_hash`), hot-state root (`hot_state_checkpoint_hash`), or, once `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`NATIVE_POSITION` is enabled, the position-state root (`position_state_checkpoint_hash`) — caused by a VM/state-computation bug, a state-merkle corruption, a subtly wrong write-set-to-state-tree materialization, or divergent hot-state promotion logic — will not be caught by any of these tools. All of them will report "verified"/"match" even though the actual Merkle root of the durable state differs from the one covered by the ledger-info signatures.

### Impact Explanation
This breaks the "proof-integrity" invariant that authenticated roots (state root, hot-state root, position-state root) bound into `TransactionInfo`/the transaction accumulator must be independently reproducible from re-execution. Its practical consequences:
- `db-tool replay_on_archive` / `replay_verify`, the tool used to detect state-divergence bugs (including hard-fork-class bugs) between historical execution and the canonical chain, can pass even when the recomputed state root is wrong, hiding a real consensus-breaking divergence instead of surfacing it.
- `ChunkExecutor::verify_execution`, used by state-sync verification tooling, gives the same false assurance for chunk-execution/apply-and-verify flows.
- Since `COMPUTE_TRADING_NATIVE_STATE_ROOTS` explicitly intends to make the position-state root consensus-verified via `TransactionInfoV1`, shipping/enabling that feature while this comparator still ignores `position_state_checkpoint_hash` (and `state_checkpoint_hash`/`hot_state_checkpoint_hash`) means the "consensus-verified" claim is not actually enforced by any of the tooling meant to enforce it — a wrong root can be accepted as valid by every downstream verification consumer of `ensure_match_transaction_info`.

This matches the gate's "wrong accumulator root/state proof accepted as valid" and "hard-fork-only divergence during commit, replay, restore, or proof verification" criteria, since the gap is specifically about state/position root divergence going undetected by the designated verification mechanism.

### Likelihood Explanation
The gap is unconditional (always present, not merely a rare edge case) whenever `ensure_match_transaction_info` is the only check used — as it currently is in all cited call sites. It does not require any privileged actor: any bug or storage inconsistency that makes local state materialization diverge from the canonical chain (e.g., a defect in JMT update logic, hot-state promotion, or position-state extension) will not be flagged. The code author flagged this explicitly with a `TODO`, which corroborates that this is a known, real gap rather than a false positive, and it is currently gated only by "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" without an accompanying enforcement change or feature-flag guard in the function itself.

### Recommendation
Extend `ensure_match_transaction_info` to compare the caller-recomputed state checkpoint hash (and, when applicable, hot-state and position-state checkpoint hashes) against `txn_info.state_checkpoint_hash()` / `txn_info.hot_state_checkpoint_hash()` / `txn_info.position_state_checkpoint_hash()`, threading the locally computed roots into the function (or a wrapper) at each of the call sites (`ChunkExecutor::verify_execution`, `replay_on_archive.rs`, `aptos-debugger`, `aptos-move/cli`) before treating a replay/apply as verified. At minimum, gate `COMPUTE_TRADING_NATIVE_STATE_ROOTS` from being enabled until this validation is implemented, as the existing comment recommends.

### Proof of Concept
1. Enable (or simulate a bug path that triggers, independent of the feature flag) a state materialization defect that changes the final Sparse-Merkle-Tree/position-tree root without altering the write-set contents that feed `state_change_hash` (e.g., a bug purely in how new leaves are merklized/hashed, not in what is written).
2. Run `db-tool replay-on-archive` (or `ChunkExecutor::enqueue_chunk_by_execution` verification) against the historical/authenticated `TransactionInfo` for that version.
3. Observe that `Verifier::execute_and_verify` / `ChunkExecutor::verify_execution` call `ensure_match_transaction_info`, which only compares `status`, `gas_used`, `state_change_hash` (write-set hash) and `event_root_hash` — all of which are unaffected by a pure root-hash computation bug — and returns `Ok(())`, reporting a successful, verified replay even though the recomputed `state_checkpoint_hash`/`position_state_checkpoint_hash` differs from the one signed into the ledger info. [6](#0-5)

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
