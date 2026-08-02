### Title
`TransactionOutput::ensure_match_transaction_info` skips checkpoint-hash verification, letting replay/verification accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
The external report's bug class is: a struct field that is supposed to mirror the authoritative state (token owner) is silently not kept in sync after the authoritative state changes, so downstream consumers trust a stale/wrong value. The Aptos-native analog is in `TransactionOutput::ensure_match_transaction_info`, which is the integrity check used by replay/verification tooling to confirm a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` pulled from backup/archive. This function deliberately omits comparison of the state-checkpoint-related hashes, so a real divergence between locally computed state roots and the authenticated `TransactionInfo` will not be detected.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  validates only `status`, `gas_used`, `write_set` hash (`state_change_hash`), and `event_root_hash` against the supplied `TransactionInfo`. It never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. The code contains an explicit acknowledgement of this gap: [2](#0-1) 

This comparator is the sole correctness gate used by the replay-verify tool, `storage/db-tool/src/replay_on_archive.rs`, which re-executes archived transactions with `AptosVMBlockExecutor` and calls `executed_outputs[idx].ensure_match_transaction_info(...)` to decide whether the replay matches the authenticated, backed-up `TransactionInfo`: [3](#0-2) . Because the comparator never checks `state_checkpoint_hash` (the field that binds a `TransactionInfo` to the actual Jellyfish Merkle / state root at that version) or the newer `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` fields, a transaction that produces the *correct* write set and events but an *incorrect* resulting state root (e.g., due to a state-checkpoint computation bug, a hot-state or position-state divergence, or a targeted manipulation of state-checkpoint materialization) will pass verification unnoticed.

The actual commit path (`execution/executor/src/workflow/do_ledger_update.rs`, `do_state_checkpoint.rs`) does compute and embed these checkpoint hashes into the `TransactionInfo` fed into the transaction accumulator, so the live consensus/execution path is not directly affected. The vulnerability is specifically that the *authenticated* verification mechanism used to detect divergence during replay/restore/hard-fork-verification silently ignores the very fields (`state_checkpoint_hash`, etc.) that bind a `TransactionInfo` to the correct state root.

### Impact Explanation
This falls squarely in the in-scope categories: "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Wrong accumulator root ... accepted as valid." If any future feature (the comment references `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, hot-state, or position-state checkpointing) or any bug in state-checkpoint materialization causes the locally computed state root to diverge from the historically committed one, `replay_on_archive`'s comparator would still report success, because it structurally cannot detect the mismatch. This gives false assurance that a node's replayed ledger state is byte-for-byte consistent with the authenticated backup/archive data, undermining the primary tool operators use to detect state divergence (e.g., after upgrades, during hard-fork validation, or when auditing archival nodes). A wrong state root being "accepted as valid" by this tool is a proof-integrity violation as defined by the state-integrity gate.

### Likelihood Explanation
The gap is unconditional and always present in the current code — it's not merely theoretical, since the comment explicitly documents that the comparator is missing this check pending a feature flag rollout. Any bug in state-checkpoint hash computation, hot-state root aggregation, or position-state checkpoint hash calculation (all fairly new, actively developed subsystems per the surrounding code) would go completely undetected during replay verification, with no additional runtime precondition needed beyond running the `replay_on_archive`/`replay-verify` tool.

### Recommendation
Extend `ensure_match_transaction_info` to also assert equality between the locally computed `state_checkpoint_hash` (and, when present, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) and the corresponding fields on the supplied `TransactionInfo`, for every transaction that is a state checkpoint (i.e., `has_state_checkpoint_hash()` is true). Do this unconditionally rather than gating it behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, since replay verification should always be able to detect a state-root divergence, not just once a specific feature is enabled.

### Proof of Concept
1. In `storage/db-tool/src/replay_on_archive.rs`, construct an `expected_txn_info` (from backup) with a `state_checkpoint_hash` of `H1`.
2. Execute the same transaction locally such that the write set and events match exactly (so `state_change_hash` and `event_root_hash` checks pass), but the resulting materialized state checkpoint hash computed by `DoStateCheckpoint` is `H2 != H1` (simulable via any bug/manual corruption in state-checkpoint materialization, hot-state root aggregation, or position-state checkpoint calculation).
3. Call `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_info, Some(&expected_writeset), Some(&expected_event))` as done in [4](#0-3) .
4. Observe the call returns `Ok(())` (no error) despite `H1 != H2`, because [1](#0-0)  never compares `state_checkpoint_hash` fields — confirming that a genuine state-root divergence is silently accepted as a valid replay.

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
