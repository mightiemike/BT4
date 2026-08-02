## Analysis Result

This is a genuine, self-documented gap: `TransactionOutput::ensure_match_transaction_info` in `types/src/transaction/mod.rs` is the authenticated-replay integrity gate used by `storage/db-tool/src/replay_on_archive.rs` (`Verifier::execute_and_verify`) and `execution/executor/src/chunk_executor/mod.rs` to confirm that a locally re-executed transaction matches the historical, signature-covered `TransactionInfo` before accepting a chunk as "verified." [1](#0-0) 

### Title
`ensure_match_transaction_info` omits state/hot-state/position checkpoint hash comparison, letting replay-verify accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`ensure_match_transaction_info` checks status, gas used, write-set hash (`state_change_hash`), and event root hash against the trusted `TransactionInfo`, but explicitly skips comparing the state checkpoint hash, hot-state checkpoint hash, and `position_state_checkpoint_hash`. The code itself documents this omission via a `TODO(trading-native)` comment. Any replay/verification tool relying solely on this function (currently `storage/db-tool/src/replay_on_archive.rs`) can report a transaction chunk as successfully verified even when the locally computed state root diverges from the authenticated one.

### Finding Description
`ensure_match_transaction_info` is the sole comparator used by `replay_on_archive`'s `execute_and_verify` to accept re-executed output as matching the archived, ledger-info-signed `TransactionInfo`: [2](#0-1) 

Within the comparator, only `status`, `gas_used`, write-set hash, and event root hash are asserted; the trailing comment acknowledges that checkpoint hashes (state, hot-state, and the new `position_state_checkpoint_hash` introduced for the "trading-native" position-state feature) are not validated: [3](#0-2) 

`TransactionInfo` (V1) carries these checkpoint hashes as part of its authenticated content that feeds the transaction accumulator leaf hash (as seen in test helpers building `TransactionInfo` with `maybe_state_checkpoint_hash`/`hot_state_checkpoint_hash`), so they are part of the committed, signature-covered ledger state, yet the replay comparator silently drops them.

### Impact Explanation
If a bug in state-checkpoint computation, hot-state merkleization, or the new position-state Merkle path (`native_state_committer.rs`, `state_checkpoint_output.state_checkpoint_hashes`, etc.) produced a state root that diverges from the one committed/signed in the archived `TransactionInfo`, `replay_on_archive` — the tool whose entire purpose is to catch exactly this kind of divergence — would report success. This defeats the primary detection mechanism for state-commitment corruption in the corresponding sub-trees, satisfying the gate's "authenticated API or state-view output bound to the wrong version/root" and "replay divergence" criteria for the state/hot-state/position state paths specifically.

### Likelihood Explanation
Likelihood is limited today because `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (and the position-state feature) is gated and not fully enabled per the comment; the code itself calls out the risk as something to fix "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`." However, the vanilla state-checkpoint hash and hot-state checkpoint hash comparisons are also skipped unconditionally today for any transaction that carries one (i.e., any checkpoint/reconfiguration transaction), independent of the trading-native feature flag, so the gap is live now for those hashes, not just the future feature.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`'s freshly computed state checkpoint hash (and hot-state / position-state checkpoint hashes when present) against `txn_info.state_checkpoint_hash()` / equivalents, failing the check (and thus the replay-verify chunk) on mismatch, before any dependent feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is turned on.

### Proof of Concept
Not independently reproducible without a concrete state-tree computation bug to trigger the divergence; the finding is that the verification gate itself — by its own documented admission — would fail to detect such a bug if one existed in the state/hot-state/position checkpoint hashing paths, since `ensure_match_transaction_info` never inspects those fields.

**Note on confidence**: This is a real, in-repo acknowledged gap (verified via direct code read and the `TODO(trading-native)` comment), but its current *exploitable* severity is bounded by the fact that the position-state/trading-native feature is not yet fully live. I was not able to fully confirm within available searches whether any other, independent path (e.g., `chunk_result_verifier.rs` or `aptosdb_writer.rs`'s ledger-info root-hash checks) already re-validates the state checkpoint hash outside of `ensure_match_transaction_info`, which would reduce this to a replay-tool-only gap rather than a consensus/commit-path gap — this should be confirmed with a full Devin session before treating it as a standalone high-severity finding.

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
