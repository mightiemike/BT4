### Title
Chunk-executor / replay-verify integrity check skips the world-state checkpoint root, allowing undetected state divergence to be committed - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sanity check used to confirm that a locally re-executed transaction output matches the already-authenticated `TransactionInfo` pulled from a proof during chunk execution / state-sync / replay-verify / debugger flows. The function checks status, gas used, write-set hash, and event-root hash, but explicitly skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — i.e. it never compares the locally computed Sparse-Merkle-Tree world-state root against the authenticated one.

### Finding Description
`ensure_match_transaction_info` is defined at [1](#0-0)  and is explicitly documented as incomplete: [2](#0-1) 

The function verifies status, `gas_used`, `write_set_hash` (state_change_hash), and `event_root_hash` against the `txn_info` fields, but the `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` fields of `txn_info` — the actual Jellyfish-Merkle/Sparse-Merkle-Tree world-state roots for that checkpoint version — are never compared to anything derived from local execution.

This function is called from the production replay/state-sync paths:
- `execution/executor/src/chunk_executor/mod.rs` (chunk executor, used during fast-sync / state-sync chunk application)
- `storage/db-tool/src/replay_on_archive.rs` (archive replay-verify tooling)
- `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs` (debugging/replay CLI)

In each of these callers, this is the primary local-vs-authenticated-output equivalence check performed after re-executing a transaction whose `TransactionInfo` was already accepted from an external/untrusted source (a synced peer, an archive backup, or a debugger-supplied ledger). Because the comparator omits the state-checkpoint hash, a state-root divergence between the node's freshly computed Merkle state (e.g., produced by non-determinism, a storage/restore bug, a stale/incorrect intermediate SMT, or corrupted data supplied during replay) will not be flagged as a mismatch. The check will report success even though the locally materialized world state differs from the state that was actually agreed upon and signed by validators.

### Impact Explanation
This breaks the state-commitment integrity invariant explicitly called out in the scope ("Committed state that differs from the correct VM result or corrupts durable ledger data" / "Hard-fork-only divergence during commit, replay, restore ... accepted as valid"). A node performing chunk-based state sync or an operator using `replay_on_archive`/debugger tooling to validate historical execution can silently accept and persist a corrupted or divergent world-state root while still passing the built-in consistency check, because the one field designed to catch exactly this class of bug (`state_checkpoint_hash`) is never inspected. This can mask storage bugs, execution non-determinism, or tampered replay inputs, resulting in durable ledger corruption that goes undetected by the very check meant to prevent it.

### Likelihood Explanation
The gap is deterministic and code-documented (the TODO explicitly states the comparator ignores all three checkpoint hashes), so any scenario where local re-execution's state root legitimately or illegitimately diverges from the authenticated `TransactionInfo` will pass `ensure_match_transaction_info` unnoticed. This does not require a malicious peer for the state-integrity failure itself — the check simply cannot catch state-root divergence regardless of its cause, in code paths (`chunk_executor`, `replay_on_archive`) that run on mainnet nodes today.

### Recommendation
Extend `ensure_match_transaction_info` to compute and compare the locally derived `state_checkpoint_hash` (and, where applicable, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against `txn_info`'s corresponding fields whenever those hashes are present (i.e., at checkpoint versions), returning an error on mismatch just as is done for `write_set_hash` and `event_root_hash`, before any callers treat the check as sufficient assurance that local execution reproduced the authenticated ledger state.

### Proof of Concept
Not applicable as a transaction-level PoC — the flaw is a missing comparison in an integrity-check function; a manufactured chunk/backup whose transaction outputs replay to a different world-state root than the one recorded in the accompanying (validly proven) `TransactionInfo` would pass `ensure_match_transaction_info` without error, since the comparison of `state_checkpoint_hash` never occurs in [3](#0-2) .

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
