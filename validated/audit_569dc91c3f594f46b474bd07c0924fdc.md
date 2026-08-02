### Title
`ensure_match_transaction_info` ignores state/hot-state/position checkpoint hashes, letting replay-verify accept a diverged position/state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` (`types/src/transaction/mod.rs`) is the sole comparator used by replay/debug tooling to confirm that a freshly re-executed `TransactionOutput` matches the archived, ledger-committed `TransactionInfo`. It checks status, gas, write-set hash, and event root hash, but explicitly skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — a gap the code itself documents as unresolved.

### Finding Description [1](#0-0) 

The function verifies status, gas used, write-set hash, and event root hash against the `TransactionInfo`, but the final block is a bare comment admitting the gap: [2](#0-1) 
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```

This comparator is the integrity oracle consumed by:
- `storage/db-tool/src/replay_on_archive.rs::execute_and_verify`, which re-executes archived transactions and calls `ensure_match_transaction_info` per transaction to decide pass/fail for the whole replay job. [3](#0-2) 
- `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs`, both of which use the same call for local replay validation.

Because the comparator never checks `state_checkpoint_hash` (main JMT root), `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, any of the following divergences are silently accepted as "matching":
- The locally re-executed state root (main SMT/JMT) differs from the one baked into the committed `TransactionInfo`.
- The position-state (native trading) Merkle root computed locally diverges from the authenticated one in the ledger, per `TransactionInfoV1::position_state_checkpoint_hash` (`types/src/transaction/mod.rs:2453`).
- The hot-state checkpoint root diverges similarly.

The `TransactionInfo` object is exactly what is bound into the transaction accumulator and authenticated by ledger-info signatures (see `TransactionInfoWithProof::verify` / `verify_transaction_info` in `types/src/proof/mod.rs:39-61` and `types/src/proof/definition.rs:829-875`), so these checkpoint-hash fields are part of the authenticated, hard-fork-relevant commitment. `ensure_match_transaction_info` is the one place meant to independently re-derive and cross-check that commitment during replay/debug, and it deliberately omits exactly the fields most sensitive to state divergence (state roots), while checking the less state-sensitive write-set/event hashes.

### Impact Explanation
Replay-verify (`replay_on_archive`) and the Move debugger are the primary integrity gates used to detect execution/consensus divergence against archived history (e.g., during protocol upgrades, hard forks, or auditing state-sync correctness). If a node's local execution produces a different main-state, hot-state, or position-state root than what was actually committed on-chain — due to a bug in state-checkpoint computation, a corrupted snapshot, or a consensus-breaking VM change — `ensure_match_transaction_info` will still report success as long as gas/status/write-set/event hashes happen to match. This means a real state-root divergence (the class of bug that causes chain splits) can go completely undetected by the verification tooling that exists specifically to catch it, undermining confidence in "verified" historical replays and potentially allowing an undetected hard-fork-class bug (e.g. in the newer position-state / native trading feature gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) to ship unnoticed.

### Likelihood Explanation
This is not a remotely triggerable, financially-exploitable bug against consensus itself (the accumulator/proof verification paths elsewhere, e.g. `types/src/proof/definition.rs`, `TransactionOutputListWithProof::verify`, still fully verify state-change/event hashes against ledger-signed `TransactionInfo`s). The gap is confined to the replay/debug utility's local equality check, which is only exercised by operators running `replay_on_archive`/debugger tooling, not by the consensus or storage commit path. It requires that a real state-root divergence already exists (e.g. from a separate bug) for this gap to matter; it is a detection blind-spot rather than a primary write-path corruption bug. Likelihood of being the root cause of an incident is therefore moderate — it's a documented, still-open gap (own TODO says "validate before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), meaning the feature that most needs this check is not yet safely gated on it being fixed.

### Recommendation
In `ensure_match_transaction_info` (`types/src/transaction/mod.rs`), before returning `Ok(())`, recompute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on the transaction/checkpoint boundary) against `txn_info`'s corresponding fields, mirroring the same `ensure!` pattern already used for `write_set_hash`/`event_root_hash`. Gate `COMPUTE_TRADING_NATIVE_STATE_ROOTS` enablement on this check being implemented, per the existing TODO.

### Proof of Concept
Not independently exploitable against consensus/mainnet state — this is a detection-gap finding, illustrated purely by code inspection:
1. Archive a transaction whose committed `TransactionInfo` has `state_checkpoint_hash = H1` (or a `position_state_checkpoint_hash`).
2. Run `replay_on_archive` (or the AptosVM debugger) against a local DB/VM build where a state-checkpoint computation bug or divergent position-state Merkle computation produces root `H2 != H1`, but the write set, events, gas, and status remain identical.
3. `execute_and_verify` → `ensure_match_transaction_info` (`storage/db-tool/src/replay_on_archive.rs:392-397`) returns `Ok(())` because the checkpoint-hash fields are never compared, and the tool reports the replay as fully verified despite the state-root divergence.

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
