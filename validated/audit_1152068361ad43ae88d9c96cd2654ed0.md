### Title
`ensure_match_transaction_info` skips state/hot-state/position checkpoint hash verification, allowing replay-verify to accept a corrupted state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` in [1](#0-0)  is the authenticated-comparison routine used by `db-tool`'s `replay_on_archive` verifier to confirm that locally re-executed transaction outputs match the trusted, backup-sourced `TransactionInfo`. It checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents with a `TODO(trading-native)` comment.

### Finding Description
`TransactionInfo` (both `TransactionInfoV0` and `TransactionInfoV1`) carries multiple root-hash fields that authenticate different parts of ledger state at commit time: `state_change_hash` (write-set hash), `event_root_hash`, `state_checkpoint_hash` (Sparse/Jellyfish Merkle world-state root), and, in `V1`, `hot_state_checkpoint_hash` and `position_state_checkpoint_hash` — see the struct definitions at [2](#0-1) .

The verification function `ensure_match_transaction_info` is supposed to bind a freshly-computed `TransactionOutput` to this authenticated `TransactionInfo`. It validates status, gas, write-set hash, and event-root hash [3](#0-2) , but the checkpoint-hash fields are left unchecked, with the comment explicitly acknowledging the gap:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [4](#0-3) 

This function is the sole correctness check used by `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify`, which re-executes archived transactions via `AptosVMBlockExecutor` and calls `ensure_match_transaction_info` against the backup-provided `expected_txn_infos[idx]` to decide pass/fail for each replayed transaction [5](#0-4) .

Because the checkpoint hashes are the fields that summarize the Merkle root of the entire world state (and the "hot state"/"position state" auxiliary roots used by newer trading-native features), a local re-execution that produces the correct write-set and events but a *different* Merkle state root (state_checkpoint_hash) than the archived TransactionInfo will not be flagged. `write_set_hash`/`event_root_hash` equality does not imply `state_checkpoint_hash` equality, since the checkpoint hash is computed from the full accumulated state tree, not from the individual transaction's write set alone (write ops are applied on top of previous state; a divergence introduced earlier, or a bug in Merkle-tree update logic, would manifest only in the checkpoint hash).

### Impact Explanation
This breaks the "committed state that differs from the correct VM result... must be detected" invariant for the replay/verify tooling: `replay_on_archive` is explicitly the tool used to authenticate that a target/archive DB's committed ledger state matches independent local VM execution. Silently ignoring the state/hot-state/position checkpoint hashes means a state-root divergence (e.g., from a state-sync bug, storage corruption, or a subtly incorrect Merkle-tree update in a hard-fork/upgrade scenario) will be reported as a successful replay-verify pass. This is a proof-integrity failure specifically in a "replay" and "authenticated-response" path called out in the Gate as in-scope ("Hard-fork-only divergence during commit, replay, restore, or proof verification").

However, this tool is a diagnostics/operational utility (`db-tool`), not part of consensus-critical commit/execution enforcement — it does not itself corrupt the ledger; it only fails to catch corruption/divergence that already exists elsewhere. Its severity is bounded by the fact that mainnet safety does not depend on `db-tool` succeeding; it is used by node operators for optional verification. I could not find evidence within this codebase that this verifier gates any state-sync/fast-sync acceptance path that mainnet security relies on (that binding, if any, is only implied by the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature-flag comment, whose consumer at `storage/aptosdb/src/db/aptosdb_reader.rs` I was not able to fully trace given remaining iteration budget).

### Likelihood Explanation
The bug is deterministic and always present (not a race or edge case) — any code path that calls `ensure_match_transaction_info` will always skip checkpoint-hash comparison, as shown by the code itself. Triggering the actual "state root diverges silently" scenario requires an underlying execution/state divergence to exist in the first place (this check is not itself the source of corruption, just a monitoring gap); the report authors' own comment confirms the developers are aware and intentionally gating the fix behind enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on either side) before returning `Ok(())`, matching the guidance already given in the in-repo TODO. Ensure `replay_on_archive` and any other callers pass through the locally-computed checkpoint hashes so a genuine mismatch is surfaced as a verification failure rather than silently passing.

### Proof of Concept
Conceptual (no exploit harness available in this read-only review):
1. Run `db-tool replay-on-archive` against an archive/target DB range where a transaction's write set and events are unchanged from a legitimate replay, but the resulting world-state Merkle root (`state_checkpoint_hash`) differs — e.g., due to a pre-existing divergence in prior transactions' state application, or a bug that corrupts unrelated state slots without touching the events/write-set of the checkpoint-marking transaction itself.
2. `Verifier::execute_and_verify` calls `ensure_match_transaction_info(version, expected_txn_info, ...)` at [6](#0-5) .
3. Because status/gas/write_set_hash/event_root_hash all still match, `ensure_match_transaction_info` returns `Ok(())` at [7](#0-6)  even though `state_checkpoint_hash` (and potentially `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) mismatch — these fields are never read/compared.
4. The tool reports the replay as verified, hiding the ledger-state divergence from operators.

Note: I was unable to fully confirm (within remaining budget) whether any consensus- or sync-critical path other than `db-tool`'s `replay_on_archive` consumes this exact comparator, which limits certainty about broader mainnet impact beyond the diagnostics tool itself.

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
