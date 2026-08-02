## Finding

### Title
Replay-verify tooling accepts divergent state roots because `ensure_match_transaction_info` skips checkpoint-hash validation - (File: `types/src/transaction/mod.rs`)

### Summary
The `TransactionOutput::ensure_match_transaction_info` function is the sole integrity check used by `db-tool`'s `replay_on_archive` (and `aptos-debugger`) to confirm that a freshly re-executed transaction output matches the previously committed, archived `TransactionInfo`. The function validates status, gas, write-set hash, and event root hash, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that actually authenticate the resulting global state (Merkle/Jellyfish root), as opposed to the per-transaction write set. This is analogous to the Putty finding: a safety check ("does the recipient/resulting state match what's expected") that other code paths assume is performed, is silently skipped, allowing a bad/divergent state to be accepted as valid.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  checks the execution status, gas used, write-set hash (`state_change_hash`) and event root hash against a given `TransactionInfo`, but the function contains an explicit code comment acknowledging the gap: [2](#0-1) 

This comparator is invoked directly as the authoritative correctness check in `replay_on_archive.rs`'s `execute_and_verify`, which re-executes archived transactions and compares the freshly computed `TransactionOutput` to the expected `TransactionInfo` pulled from backup/archive: [3](#0-2) 

Because `state_change_hash` only commits to the **write set** of a single transaction (not the resulting global state tree), and `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` are the fields that commit to the actual Sparse-Merkle/Jellyfish state root and the new "trading-native" position state root, skipping them means: two executions can produce byte-identical write sets and events, yet the resulting state tree (e.g., due to a state-merklization bug, hot-state promotion bug, or the new position-state-checkpoint subsystem referenced by `position_state_checkpoint_hash`) can diverge from the value stored on-chain, and `ensure_match_transaction_info` will still report success.

### Impact Explanation
`replay_on_archive` / `replay-verify` is the safety net Aptos uses to detect state divergence between a candidate binary/state-transition and the historical, agreed-upon ledger before it is trusted for consensus/hard-fork decisions. If this tool structurally cannot detect a divergent state checkpoint hash, a bug that corrupts the state tree (but preserves write-set bytes/events/gas/status) would pass replay-verify undetected. This falls squarely into the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "committed state that differs from the correct VM result" impact categories: the checked invariant (state root matches expected state root) is provably not enforced by this function, even though its purpose (per doc comments) is to be that check.

### Likelihood Explanation
This is not a hypothetical: the code comment itself documents the exact scenario ("replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution") and gates the fix behind enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`. Since the feature flag exists and is referenced in `storage/aptosdb/src/db/aptosdb_writer.rs` and `types/src/on_chain_config/aptos_features.rs`, this indicates the checkpoint-hash computation is under active rollout, and the verification gap is real for any version range where state/position checkpoint hashes are produced but not compared during replay-verify.

### Recommendation
Extend `ensure_match_transaction_info` to compute and compare `state_checkpoint_hash` (and `hot_state_checkpoint_hash`/`position_state_checkpoint_hash` when applicable) against the expected `TransactionInfo`, consistent with how `write_set_hash` and `event_root_hash` are already validated, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled by default in replay-verify-critical paths.

### Proof of Concept
Not applicable as a runtime exploit — the flaw is a static code-level omission, provable directly from the function body: `ensure_match_transaction_info` ( [1](#0-0) ) never calls `.state_checkpoint_hash()` for comparison purposes, only reads it in the surrounding doc/TODO comment, while its only two callers (`replay_on_archive.rs::execute_and_verify` and `aptos-debugger`) rely on it as the pass/fail gate for replay verification.

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
