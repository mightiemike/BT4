I have found a solid analog. This is used in `storage/db-tool/src/replay_on_archive.rs:392` (the `replay-verify` archival-integrity tool) and `aptos-move/aptos-debugger/src/aptos_debugger.rs` (debugger mismatch reporting), and the gap is explicitly acknowledged in a TODO in the source itself.

### Title
`ensure_match_transaction_info` omits state/hot-state/position checkpoint hash checks, allowing replay-verify to certify a diverged state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative comparator used by replay/debug tooling to confirm that a locally re-executed `TransactionOutput` matches the authenticated on-chain `TransactionInfo` for a given version. It validates status, gas used, write-set hash (`state_change_hash`), and event root hash, but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`.

### Finding Description [1](#0-0) 

The function compares four fields between the locally computed `TransactionOutput` and the trusted, proof-verified `TransactionInfo`: `status`, `gas_used`, `state_change_hash` (write-set hash), and `event_root_hash`. It never compares `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` against anything computed from local execution. The comment directly above `Ok(())` states this is a known, unaddressed gap: [2](#0-1) 

This is consumed by `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which is the tool operators use to independently re-execute a chunk of history against an archival node and assert that local execution reproduces the authenticated ledger exactly: [3](#0-2) 

Because `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` are the fields that bind a `TransactionInfo` to the actual JMT/hot-state/position-state root produced by execution (and thus flow into the transaction-accumulator root that ledger infos commit to), a divergence in any of these roots — from a state-computation bug, storage corruption, or a malicious archival data source feeding `replay_on_archive` — would not be caught by this comparator. `replay_on_archive` would report a clean "replay matches" result even though the state root actually differs from what local execution produced.

### Impact Explanation
This is a proof/authenticity-verification gap in the replay-verification tool path, not a consensus-critical commit-path bug: the on-chain accumulator/proof machinery itself (`TransactionInfoListWithProof::verify`, `TransactionAccumulatorProof::verify`) still cryptographically binds `TransactionInfo.hash()` — which includes all these fields — to the accumulator root, so a validator cannot forge an accepted `TransactionInfo` with a wrong checkpoint hash without breaking BLS/consensus signatures. The concrete, currently-exploitable impact is narrower: `replay_on_archive` / the debugger's mismatch printer are the tools operators and auditors rely on to detect a state-divergence bug (e.g. a hard-fork-class VM bug, or the new `COMPUTE_TRADING_NATIVE_STATE_ROOTS` position-state feature diverging from consensus) by re-executing history against an archive. Silently ignoring the checkpoint-hash fields means this safety net cannot detect divergence in the periodic state root, hot-state root, or the new native-position state root — exactly the class of bug this tool exists to catch. The code's own TODO explicitly calls this out as blocking `COMPUTE_TRADING_NATIVE_STATE_ROOTS` from being safely enabled.

### Likelihood Explanation
Low-to-moderate today: `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is gated behind on-chain feature flags requiring `TRANSACTION_INFO_V1` and `HOTNESS_IN_EPILOGUE`, so `position_state_checkpoint_hash` is not currently populated in production per `types/src/on_chain_config/aptos_features.rs`/`types/src/block_executor/config.rs`. However, `state_checkpoint_hash` is populated today in all networks (it's the ordinary per-checkpoint SMT root), and the gap already silently weakens `replay_on_archive`'s ability to catch state-root divergence bugs in current mainnet operation, not merely in a future feature.

### Recommendation
Extend `ensure_match_transaction_info` to also validate `state_checkpoint_hash` (when the local output represents/ends a checkpoint), `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally recomputed roots, or explicitly document/assert that callers of this function are barred from claiming full replay parity until these are wired in — matching the recommendation pattern of "create the dedicated correct-path function and only invoke it once it's complete" from the seed report.

### Proof of Concept
Not directly exploitable as a memory/consensus safety bug; the demonstration is by code inspection: construct a `TransactionOutput` whose write set/events/gas/status match a given `TransactionInfo` but whose resulting state root (as it would be computed at checkpoint time) differs from `txn_info.state_checkpoint_hash()`. Call `ensure_match_transaction_info` — it returns `Ok(())` despite the state-root mismatch, and `replay_on_archive`'s `execute_and_verify` at [4](#0-3)  will treat the chunk as successfully verified.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L392-405)
```rust
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
```
