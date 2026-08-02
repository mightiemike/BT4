## Finding

### Title
`TransactionOutput::ensure_match_transaction_info` skips state-checkpoint hash comparison, letting replay-verify accept a corrupted state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole correctness check used by the `db-tool replay-on-archive` verifier (and other replay/debugger tools) to confirm that locally re-executed transactions match the backed-up/archived `TransactionInfo`. The function validates status, gas, write-set hash, and event-root hash, but explicitly does **not** validate the `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` fields, which represent the authenticated Merkle root of world state. This means a diverging or corrupted state root produced during replay is never flagged as a mismatch.

### Finding Description
`ensure_match_transaction_info` compares the recomputed `TransactionOutput` against the archived `TransactionInfo` on four axes only — status, gas used, write-set hash, and event-root hash: [1](#0-0) 

The function's own comment discloses the gap:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [2](#0-1) 

This routine is used directly by `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify` as the pass/fail gate for each replayed transaction chunk pulled from a backup archive: [3](#0-2) 

The `expected_txn_info` values fed into this check come straight from the backup handler's transaction iterator, i.e., from potentially untrusted archive/CDN storage, not from a value that has already been proof-verified against a signed `LedgerInfo` at the point this comparison runs.

Because `state_checkpoint_hash` is skipped, a transaction whose locally recomputed Sparse/Jellyfish Merkle root diverges from the archived value (due to a state-computation bug, a corrupted archive entry, or a hard-fork-only VM/state-tree divergence) will still pass `ensure_match_transaction_info` as long as the write set bytes and events match. The tool will report "replay verified" while the authenticated state root is actually wrong.

### Impact Explanation
This breaks the "committed state must match correct VM result" and "replay must preserve deterministic proof binding" invariants for the one tool whose job is precisely to detect such divergence. A wrong state root that reaches consensus (e.g., via a subtle state-tree/replication bug affecting only some node software, or a corrupted/tampered backup source) would go undetected by `replay-verify`, defeating the safety net operators rely on to catch hard-fork-class state divergence before it silently corrupts durable ledger data across the network. This matches the in-scope class: "Hard-fork-only divergence during commit, replay, restore, or proof verification."

### Likelihood Explanation
The gap is unconditional and always active — it is not feature-flagged (`COMPUTE_TRADING_NATIVE_STATE_ROOTS` is never referenced from this function itself; it's only mentioned as the flag whose enablement the TODO says must first be preceded by fixing this check). Any run of `db-tool replay-on-archive`, `aptos-debugger`, or the CLI paths calling `ensure_match_transaction_info` today inherits this blind spot, so exploitation does not require any special conditions beyond a state-root divergence existing in the replayed data.

### Recommendation
Extend `ensure_match_transaction_info` to recompute the state checkpoint hash (and hot-state / position-state checkpoint hashes when applicable) from the replayed output and compare them against `txn_info.state_checkpoint_hash()` (and the V1 equivalents), returning an error on mismatch, before this check is relied upon as a state-integrity guarantee by any replay/verification tooling.

### Proof of Concept
Not directly exploitable as a transaction the attacker submits; rather, it is demonstrated by code inspection: feed `replay_on_archive`'s `Verifier` a backup transaction whose `TransactionInfo.state_checkpoint_hash` is set to an arbitrary/incorrect value while write set bytes, events, gas, and status are left correct — `execute_and_verify` calls `ensure_match_transaction_info`, which will return `Ok(())` because it never inspects `state_checkpoint_hash`, so the corrupted checkpoint hash is reported as "verified." [4](#0-3)

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
