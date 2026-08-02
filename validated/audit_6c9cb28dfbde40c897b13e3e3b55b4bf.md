I have enough to write the finding now. The bug is self-documented in the code itself (a TODO acknowledging the gap), but let me verify the actual write_set_hash check does NOT cover state_change_hash including state roots — confirming the checkpoint hash is a genuinely separate, unchecked field — and identify who else relies on this for hard-fork-style divergence detection.### Title
`ensure_match_transaction_info` omits state/hot-state/position checkpoint hash validation, allowing corrupted state roots to pass replay-verification - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity check used by replay/verification tooling (`db-tool`'s `replay_on_archive`, `aptos-debugger`, and the CLI's transaction-replay command) to confirm that a locally re-executed transaction produced the same result as the transaction committed to the authenticated ledger (`TransactionInfo`). The function checks status, gas used, write-set hash (`state_change_hash`), and event root hash — but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This is acknowledged directly in the code's own TODO comment.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates four properties of a re-executed `TransactionOutput` against the authenticated `TransactionInfo`: `status`, `gas_used`, `write_set_hash` (against `state_change_hash`), and `event_root_hash`. It explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, as stated in its own trailing comment: [2](#0-1) 

`TransactionInfo` carries these checkpoint hashes as the periodic (per-checkpoint, not per-transaction) commitments of the global Jellyfish Merkle state root, the hot-state root, and — once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled — the native "position" state root ( [3](#0-2) ). These roots are exactly the "committed state" invariant that must survive executor-to-storage handoff and be provably bound to the correct ledger version.

The check is invoked from the two real integrity-verification call sites:
- `storage/db-tool/src/replay_on_archive.rs`, which re-executes an archived history and calls `ensure_match_transaction_info` per transaction to assert the replay is byte-for-byte state-equivalent to the authenticated archive [4](#0-3) .
- `aptos-move/aptos-debugger/src/aptos_debugger.rs`'s `print_mismatches`, used to surface divergences between locally computed outputs and expected `TransactionInfo`s [5](#0-4) .

Because the write-set hash (`state_change_hash`) only commits to the transaction's own write set, not to the resulting global/hot/position Merkle roots, a divergence in state root computation (e.g., a state-summary bug in `do_state_checkpoint.rs`, a JMT/SMT drift, a position-state extension bug, or a corrupted historical checkpoint value) would be invisible to this check as long as the write set and events for that individual transaction still match. The dedicated defense against this class of bug elsewhere in the codebase — `storage/aptosdb/src/sharded_jmt_merkle_db.rs`'s `merklize_snapshot`, which asserts JMT root == SMT root at commit time ( [6](#0-5) ) — only guards internal consistency of a single node's live computation. It does not protect the replay/verify tooling path, which is specifically meant to independently re-derive and cross-check state roots against externally supplied, signed `TransactionInfo`s from a different source (e.g., an archive or another node).

### Impact Explanation
Replay-verification is one of the primary tools operators and auditors use to confirm that a historical/backup ledger's committed state matches independent re-execution — i.e., to catch state-commitment bugs, corrupted backups, or non-determinism after the fact. Because `ensure_match_transaction_info` silently skips the state/hot-state/position checkpoint-hash comparison, a state root that has been corrupted (through a local computation bug, a tampered backup/archive `TransactionInfo` stream, or any consensus/committed-state divergence that doesn't happen to also change the same transaction's own write set) will be reported as a successful, verified replay. This directly violates the state-integrity gate requirement that "committed state that differs from the correct VM result... " and "wrong ... state proof accepted as valid" must be detected — here the verification path that exists specifically to catch that class of bug is a no-op for state roots. This is a high-impact silent-verification-bypass affecting the trustworthiness of ledger backups/restores and of any hard-fork-adjacent state divergence, though the immediate consequence is failure-to-detect rather than direct fund loss.

### Likelihood Explanation
Likelihood is inherent, not adversary-triggered: it fires automatically any time `ensure_match_transaction_info` is used and a checkpoint-hash-only divergence exists (e.g. a bug affecting only how the periodic state/hot-state/position root is folded, not the individual write set). The comment shows this gap is a known, currently-tracked TODO ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), meaning the position-state root check is entirely unvalidated even though the codebase is actively building out `COMPUTE_TRADING_NATIVE_STATE_ROOTS` support (see `do_state_checkpoint.rs`'s `compute_position_checkpoint`). No special privileges or attacker action are required — an operator or auditor simply won't get an accurate signal from the standard replay-verify tool.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`-derived (or caller-supplied, since `TransactionOutput` alone lacks the surrounding checkpoint context) `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the corresponding fields on `txn_info` whenever they are `Some` on the expected side, mirroring the pattern already used in `DoStateCheckpoint::get_state_checkpoint_hashes`'s known-hash validation ( [7](#0-6) ). At minimum, gate enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in production on this fix landing first, as the code comment itself recommends.

### Proof of Concept
Not directly exploitable as a state-mutation PoC (it is a verification-omission bug); the logical PoC is:
1. Produce a `TransactionInfo` (or backup/archive stream) whose `state_checkpoint_hash` (or `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) does not correspond to the actual resulting state after applying the transaction's own write set (e.g., corrupt just the checkpoint hash field of an otherwise valid `TransactionInfoV1`, or introduce a state-summary computation bug that changes the root without changing any individual write set).
2. Run `storage/db-tool/src/replay_on_archive.rs`'s replay/verify flow, which calls `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_infos[idx], ...)` per transaction [4](#0-3) .
3. Observe that `status`, `gas_used`, `write_set_hash`, and `event_root_hash` all match (since only the checkpoint hash was corrupted/diverged), so `ensure_match_transaction_info` returns `Ok(())` and the tool reports the transaction as successfully verified, even though the actual committed/authenticated global state root diverges from what local re-execution would produce.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L392-405)
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

**File:** storage/aptosdb/src/sharded_jmt_merkle_db.rs (L504-510)
```rust
        assert_eq!(
            root_hash,
            smt.root_hash(),
            "JMT vs SMT root hash mismatch — scratchpad/JMT drift detected: jmt={}, smt={}",
            root_hash,
            smt.root_hash()
        );
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L206-220)
```rust
        if let Some(known) = known_state_checkpoints {
            ensure!(
                known.len() == num_txns,
                "Bad number of known {label} hashes. {} vs {}",
                known.len(),
                num_txns,
            );
            if let Some(idx) = last_checkpoint_index {
                ensure!(
                    known[idx] == Some(computed_last_checkpoint_hash),
                    "{label} root hash mismatch with known hashes passed in. {:?} vs {:?}",
                    known[idx],
                    Some(computed_last_checkpoint_hash),
                );
            }
```
