## Finding [1](#0-0) 

### Title
`TransactionOutput::ensure_match_transaction_info` silently ignores state-checkpoint hashes, allowing replay-verify to accept a divergent committed state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info`, the function used by replay-verify tooling to confirm that a freshly re-executed transaction matches the transaction info recorded in the durable/backup ledger, only checks status, gas used, write-set hash, and event-root hash. It deliberately skips the `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` fields of `TransactionInfo`, which are exactly the fields that bind a transaction to the authenticated world-state (Sparse Merkle Tree / hot-state / native-position-state) root.

### Finding Description
`ensure_match_transaction_info` computes and compares only three quantities against the persisted `TransactionInfo`:
- `status`
- `gas_used`
- `write_set_hash` vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()` [2](#0-1) 

It never recomputes or compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` carried by `TransactionInfoV0`/`TransactionInfoV1`: [3](#0-2) 

The code itself documents the gap with a `TODO`, explicitly stating that this comparator "ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution," and that the hashes must be validated before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`: [4](#0-3) 

This function is the sole verification gate used by `storage/db-tool/src/replay_on_archive.rs`, the tool operators/auditors run to independently re-execute historical mainnet transactions and confirm the archived ledger (transaction infos, write sets, events) is consistent with VM re-execution: [5](#0-4) 

Because the state-checkpoint/hot-state/position-state roots are never recomputed and compared here, any divergence in those roots — whether from a state-checkpoint computation bug, a hot-state aggregation bug, or (once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / native "trading" position-state is enabled) a position-state root bug — passes replay-verify undetected as long as the write set and events still hash correctly.

### Impact Explanation
Replay-verify is the primary tool relied on to detect corruption or non-determinism in the authenticated world-state root committed to Merkle/JMT structures (`state_checkpoint_hash`), the newer hot-state root (`hot_state_checkpoint_hash`), and the position/native-trading state root (`position_state_checkpoint_hash`). If any of these roots silently diverges from the historically committed value (e.g., due to a bug in `DoStateCheckpoint`, hot-state merge logic, or the position-state subsystem introduced for trading-native support), `ensure_match_transaction_info` still reports success, because it checks only write-set hash and event-root hash, not the state-checkpoint hash itself. This defeats the purpose of the check: a wrong state/proof root is accepted as valid by the verification tool that node operators and auditors trust to catch exactly this class of bug before/after upgrades or hard forks.

### Likelihood Explanation
The gap is unconditionally present today (not gated behind a feature flag) — every call from `replay_on_archive.rs` uses this comparator, and the missing fields are simply never examined. The comment shows the aptos-core team is aware this must be fixed before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, confirming that under that (near-term) config, a genuine state-root divergence would go completely undetected. Root-hash logic (`DoStateCheckpoint`, hot-state, and position-state) is complex and evolving, making a computation bug plausible; this verification gap removes the safety net that would normally catch it.

### Recommendation
Extend `ensure_match_transaction_info` to recompute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in the expected `TransactionInfo`) against values derivable from the re-executed `TransactionOutput`/state view, mirroring how `write_set_hash` and `event_root_hash` are already validated, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or relying on replay-verify for hot-state/position-state correctness.

### Proof of Concept
1. Run `db-tool replay-on-archive` against a backup/archive range.
2. Inject (or imagine) a bug that changes the computed `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` for a transaction while leaving `write_set` and `events` (and thus their hashes) unchanged — e.g., a bug only in the SMT/hot-state root aggregation step, not in the write-set materialization itself.
3. `execute_and_verify` in `replay_on_archive.rs` calls `ensure_match_transaction_info`, which checks only `status`, `gas_used`, `write_set_hash`, `event_root_hash` (all still correct), and returns `Ok(())`.
4. The replay-verify run reports success even though the authenticated state root diverges from the archived/expected root, exactly as documented by the code's own `TODO` comment.

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
