### Title
Replay-verify tooling (`ensure_match_transaction_info`) never validates state/hot-state/position checkpoint hashes, allowing corrupted archived ledger data to pass verification — ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single comparator used by all replay-verify entry points (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) to confirm that a locally re-executed transaction matches the `TransactionInfo` recorded in an untrusted/archived backup. It checks status, gas, write-set hash, and event-root hash, but it explicitly skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — fields that bind the transaction to the authenticated global/position state root at that version.

### Finding Description
`ensure_match_transaction_info` computes and checks four things: execution status, gas used, write-set hash (`state_change_hash`) and event-root hash, then returns `Ok(())` without checking the checkpoint-hash fields, with an explicit code comment acknowledging the gap: [1](#0-0) 

Because this comparator is the *only* correctness check performed by the offline replay-verify tools, none of the checkpoint hashes carried in `TransactionInfo`/`TransactionInfoV1` (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) are ever cross-checked against locally recomputed state during replay: [2](#0-1) 

This means an archived/backup `TransactionInfo` whose write-set hash and event-root hash are correct, but whose checkpoint-hash fields have been corrupted or point to a different (wrong) global/position state tree — i.e., a divergent Jellyfish Merkle root — will still pass `db-tool replay-verify`, `aptos-debugger`, and the CLI replay commands with no error reported.

### Impact Explanation
Replay-verify against a backup/archive is the trust mechanism operators and tooling use to assert "this backup faithfully represents on-chain history" before using it to restore a node, seed a fullnode, or validate historical data for downstream consumers (indexers, explorers, other authenticated API responses derived from restored state). If the checkpoint-hash fields are never validated, a state root that is inconsistent with the correct VM execution result (particularly the position/hot-state roots introduced by `TransactionInfoV1`) can be silently accepted as "verified", defeating the entire purpose of a proof/commitment check on the restore/verification path. This satisfies the state-integrity gate's "restore flows ... must preserve deterministic proof binding" and "authenticated API ... must stay bound to the right ... root" categories, since restored state trees driven by such backups would diverge from correct state while reporting a clean verification.

### Likelihood Explanation
The gap is unconditional in the current comparator — it does not depend on any attacker action beyond supplying/serving a backup or archive with a tampered `TransactionInfo` checkpoint field; the write-set and event data (which are the fields actually checked) can independently be correct while the checkpoint hash is wrong, since they are separate fields in `TransactionInfoV1`. The code itself documents this as a known TODO, confirming the gap is real and currently shipped, not hypothetical: [3](#0-2) 
However, exploitation requires control over, or corruption of, the backup/archive data source being replay-verified — this is a verification-tooling blind spot rather than a live consensus-commit bug (normal validator consensus commit does not go through this comparator), which somewhat lowers the practical severity versus a direct on-chain state-corruption bug.

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present) against values recomputed from the locally re-executed state, consistent with how `write_set_hash` and `event_root_hash` are already checked, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or relying on this comparator for any authenticated restore/verify guarantee.

### Proof of Concept
Not independently exploitable via a network transaction — the flaw is reachable only by supplying a crafted/corrupted backup or archive (with correct write-set/event hashes but altered checkpoint-hash fields in `TransactionInfoV1`) to `db-tool replay-on-archive`, `aptos-debugger`, or the CLI replay-verify commands; the existing code path at `storage/db-tool/src/replay_on_archive.rs:392-405` will call `ensure_match_transaction_info` and report success despite the mismatched checkpoint hash, since that check is never performed. [4](#0-3) [2](#0-1)

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
