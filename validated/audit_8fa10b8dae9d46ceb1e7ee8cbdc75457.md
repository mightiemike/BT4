This confirms the candidate: `db-tool`'s `replay_on_archive.rs` `execute_and_verify` relies exclusively on `TransactionOutput::ensure_match_transaction_info` to decide whether a replayed transaction matches the archived, historically-committed `TransactionInfo` [1](#0-0) . That function only checks status, gas, write-set hash, and event-root hash, and explicitly (by its own TODO) skips the state-checkpoint/hot-state and `position_state_checkpoint_hash` fields of `TransactionInfo` before returning `Ok(())` [2](#0-1) .

### Title
Replay-verify tooling silently accepts state-root divergence because `ensure_match_transaction_info` skips checkpoint-hash fields - (File: types/src/transaction/mod.rs)

### Summary
`TransactionInfo` carries multiple authenticated roots beyond the write-set/event hashes: a state-checkpoint hash, an optional hot-state root, and (per the new "trading-native"/position feature) a `position_state_checkpoint_hash`. `TransactionOutput::ensure_match_transaction_info`, the sole correctness gate used by `db-tool`'s `replay_on_archive` verifier, never compares these fields against the locally-computed state.

### Finding Description
`ensure_match_transaction_info` verifies only `status`, `gas_used`, the write-set hash, and the event root hash [3](#0-2) . The function's own trailing comment states the gap explicitly: it "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`)," warning that "replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution" [4](#0-3) .

`replay_on_archive.rs::execute_and_verify` calls exactly this function as its only correctness check per transaction, and treats an `Ok(())` result as "this replayed output matches the archived record" [5](#0-4) . Because the state-checkpoint / hot-state / position-state root fields are never re-derived and compared, a transaction whose locally-recomputed state root (JMT state root, hot-state root, or position-state root) diverges from the archived, previously-committed value will still pass verification as long as the write-set bytes and event bytes happen to match. This can occur, for example, if a state-tree serialization/versioning change, a hot-state promotion-boundary bug, or a native "trading-native" position-tree bug produces the same logical write set but a different root hash/tree topology — precisely the kind of hard-fork-triggering, storage-schema-level divergence that state-checkpoint hashes exist to detect.

### Impact Explanation
This breaks the state-integrity invariant that "committed state that differs from the correct VM result... must be detected via authenticated roots/proofs." A node operator or auditor running `replay_on_archive` to confirm that an archived chain segment can be deterministically re-derived would get a false "PASS" even though the state root (and thus every subsequent Merkle/state proof served from that height) is wrong. Since this tool is explicitly built for verifying historical state integrity (used to detect exactly the type of non-deterministic-execution/storage bug that causes chain splits), a false negative here means a real consensus/state divergence bug could go undetected until it manifests as an actual mainnet fork, at which point diagnosis is much harder. This matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "wrong... state proof accepted as valid" impact categories.

### Likelihood Explanation
The likelihood of the *underlying* divergence occurring is separate from the certainty of the *detection gap*: the detection gap itself is code-verifiable and unconditional (the check is structurally absent, not merely disabled by a flag), so any state/hot-state/position-root corruption bug that preserves write-set bytes will not be caught by this tool. Given `hot_state_root_in_txn_info` and `compute_trading_native_state_roots` are actively-developed features referenced throughout the executor (`execution/executor/src/workflow/do_ledger_update.rs`, `execution/executor-types/src/state_checkpoint_output.rs`), the probability that a bug in those newer state-root computations exists and is missed by replay-verify is non-trivial, and the report is self-documented by the authors as a known risk rather than a hypothetical.

### Recommendation
Extend `ensure_match_transaction_info` (or add a companion check called from `replay_on_archive::execute_and_verify`) to compare the transaction's state-checkpoint hash, hot-state root, and `position_state_checkpoint_hash` against the recomputed values whenever the corresponding feature is enabled for that transaction/version, and fail loudly on mismatch instead of silently returning `Ok(())`.

### Proof of Concept
No exploit is required beyond the code path itself — the gap is a missing comparison, not a triggerable exploit condition. Conceptually: `db-tool replay-on-archive --start-version X --end-version Y` against an archive whose recorded write set/events happen to match a locally re-executed but state-root-diverging output (e.g., due to a hot-state or position-tree serialization bug) will `Ok(())` on every affected transaction in `execute_and_verify` [6](#0-5) , reporting "0 failed transactions" despite the state root being wrong.

Note: I could not fully trace whether `compute_trading_native_state_roots`/`hot_state_root_in_txn_info` are already enabled on mainnet or remain feature-gated/experimental at this time — the index did not give me a definitive on-chain feature-flag status, so likelihood of the *triggering* root-divergence bug (as opposed to the detection gap, which is unconditional) may be tempered if this is still pre-launch functionality.

### Citations

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
