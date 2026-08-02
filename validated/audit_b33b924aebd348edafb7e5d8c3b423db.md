### Title
`ensure_match_transaction_info` never validates `state_checkpoint_hash`/`hot_state_checkpoint_hash`, letting replay-verify accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole correctness check used by the replay-verify tooling to confirm that a locally re-executed transaction matches the authenticated, on-disk `TransactionInfo` that was actually committed to the ledger. The function checks status, gas used, write-set hash, and event root hash, but it never compares the `state_checkpoint_hash` (the Sparse-Merkle state root) or `hot_state_checkpoint_hash`/`position_state_checkpoint_hash` fields, a gap the code's own comment acknowledges.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  validates `status`, `gas_used`, the write-set hash against `state_change_hash`, and the event root hash against `event_root_hash`, but stops there. The trailing comment explicitly states: [2](#0-1) 

i.e. the comparator "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling ... can report a successful replay even when the authenticated ... state root diverges from local execution."

This comparator is the single verification gate used by `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes archived transactions locally and calls `ensure_match_transaction_info` on each output against the `expected_txn_infos` read from backup/archive storage: [3](#0-2) 

Because `state_checkpoint_hash` (the field that authenticates the Sparse Merkle Tree state root at that version — see `TransactionInfoV0`/`TransactionInfoV1` definitions) is never compared, this tool will report a chunk as verified/passing even if the locally computed state root diverges from the committed one, as long as the write set, events, gas, and status match. A write-set hash match does not imply the resulting state root matches (e.g., non-deterministic state-tree construction, a storage/applying bug, or corruption in the base state used for replay could produce an identical write set but a different resulting state root).

### Impact Explanation
Replay-verify against archived/backup data is a primary safety net for detecting state-commitment divergence (the class of bug that would indicate a determinism failure or hard-fork-triggering bug in execution/storage). By omitting the state-checkpoint (and hot-state/position-state checkpoint) hash comparison, this tool can silently pass runs where the actual committed ledger state root differs from what local execution recomputes, defeating its core security purpose of independently confirming the authenticity/correctness of committed state. This falls squarely in the "Hard-fork-only divergence during commit, replay, restore, or proof verification" impact category, since a real divergence in the state Merkle root — the strongest signal of an execution/storage bug — would go undetected.

### Likelihood Explanation
The gap is unconditional in the current code (it's not behind the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag that the comment references for the position-state field; `state_checkpoint_hash`/`hot_state_checkpoint_hash` are core, always-present fields per `TransactionInfoV0`/`V1` at [4](#0-3) ). Any run of `db-tool replay-on-archive` today is exposed; no privileged access or malicious actor is required — a genuine determinism bug or storage corruption is enough to be missed.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`-derived state checkpoint hash(es) against `txn_info.state_checkpoint_hash()` (and `hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()` where applicable) whenever the transaction output carries a per-transaction state root, returning an error on mismatch just as is done for `state_change_hash` and `event_root_hash`.

### Proof of Concept
Not applicable as a live PoC (this is a static/tooling code-inspection finding, not an on-chain exploit): the missing invariant is directly visible in the source —
1. `ensure_match_transaction_info` (types/src/transaction/mod.rs:2139-2204) checks `status`, `gas_used`, `write_set_hash`, `event_root_hash`, but has no `ensure!` comparing any state-checkpoint hash field.
2. `execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs:388-406` uses only this function's `Ok(())`/`Err` result to decide pass/fail for each replayed transaction.
3. Constructing an archived `TransactionInfo` (or a locally re-executed `TransactionOutput`) with identical write set/events/gas/status but a different `state_checkpoint_hash` would cause `ensure_match_transaction_info` to return `Ok(())`, causing `replay_on_archive` to falsely report the transaction as verified.

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

**File:** types/src/transaction/mod.rs (L2405-2416)
```rust
    /// The hash value summarizing all changes caused to the world state by this transaction.
    /// i.e. hash of the output write set.
    state_change_hash: HashValue,

    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,

    /// The hash value summarizing PersistedAuxiliaryInfo.
    auxiliary_info_hash: Option<HashValue>,
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
