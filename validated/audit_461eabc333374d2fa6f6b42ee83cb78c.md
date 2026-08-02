### Title
`ensure_match_transaction_info` skips state-checkpoint hash comparison, letting replay/restore verification accept a diverged state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by chunk restore, `db-tool replay_on_archive`, and `aptos-debugger` to confirm that a locally re-executed transaction matches the authenticated `TransactionInfo` pulled from a backup/archive. As implemented, it only compares `status`, `gas_used`, the write-set hash, and the event-root hash against the target `TransactionInfo` — it never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, and the code contains an explicit admission of this gap.

### Finding Description
`ensure_match_transaction_info` checks four fields only: [1](#0-0) 

The comment right before `Ok(())` documents the omission verbatim: [2](#0-1) 

This function is used as the sole state-integrity check in `db-tool`'s `replay_on_archive`, where a locally re-executed transaction output is validated against the transaction info recovered from a backup archive: [3](#0-2) 

Because `state_checkpoint_hash` (the JMT/global-state root commitment for that version) and the newer `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` fields carried by `TransactionInfoV1` are never compared, any divergence confined to state-root computation — e.g. a bug in `DoStateCheckpoint`'s Merkle-tree assembly, hot-state accumulation, or position-state summary logic that still yields byte-identical write sets and events — is invisible to this verifier. The write set and events are the *inputs* to state-root computation, not the root itself, so a bug isolated to the checkpoint/root-construction step (as opposed to VM execution) produces exactly this symptom: matching write set/events, mismatching root.

### Impact Explanation
Replay-verify tooling (`replay_on_archive`, used in the `testsuite/replay-verify` pipeline) and the chunk executor's replay/restore path (`execution/executor/src/chunk_executor/mod.rs`, which also calls `ensure_match_transaction_info`) exist specifically to catch state divergence between local execution and the authenticated, backed-up ledger. Because this check silently ignores the state-checkpoint hash, a hard-fork-only bug in state-root construction (JMT assembly, hot-state, or position-state summary logic) would pass replay-verify and chunk-restore checks as "successful," even though the locally committed state root diverges from the authenticated on-chain state root. This directly violates the required invariant that "committed state that differs from the correct VM result... must not be accepted" and that "restore paths must preserve deterministic proof binding" — the exact class of bug this verification exists to catch is undetectable by it.

### Likelihood Explanation
The gap is deterministic and always present (not a timing or race condition) — it is a straightforward comparator omission, not a hypothetical trigger. Its exploitation window is limited to a scenario where a separate bug corrupts state-root construction while leaving write sets/events untouched; the comment indicates this is a known, intentionally-tracked TODO gated behind an unlaunched feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`), suggesting the team is aware but has not yet closed it. This lowers immediate exploitability (requires a second, independent state-root bug) but keeps the safety-net itself broken for exactly the failure mode it's meant to catch.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, and — when present — `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`, against the corresponding fields of the `TransactionOutput`'s locally computed checkpoint before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, per the existing TODO. Until fixed, replay-verify and restore tooling should not be treated as authoritative proof of state-root correctness.

### Proof of Concept
Not independently reproducible without a second bug in `DoStateCheckpoint`/state-checkpoint construction (out of scope of this analog); the finding is a structural verification gap demonstrated directly by the code: [4](#0-3)  shows only status/gas/write-set-hash/event-root are `ensure!`d, with the state-checkpoint fields explicitly unchecked, and [5](#0-4)  shows this incomplete check is relied upon as the pass/fail criterion for replay verification against archived data.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2203)
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
