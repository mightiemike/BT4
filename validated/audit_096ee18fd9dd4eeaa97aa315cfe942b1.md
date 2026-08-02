## Analysis

Reducing the external report to its core invariant: an authenticated/committed value comparison must reject anything outside the exact expected set — a partial or lenient comparator lets a corrupted/divergent result pass as valid. Searching Aptos-native code for this class in proof/commit paths, the strongest local analog is in `TransactionOutput::ensure_match_transaction_info`. [1](#0-0) 

This function is the authoritative comparator used by replay/debugging tools (`db-tool`'s `replay_on_archive`, `aptos-debugger`, and the CLI) to confirm that a locally re-executed `TransactionOutput` matches the trusted, already-committed `TransactionInfo` for a given version. It explicitly checks status, gas used, write-set hash (`state_change_hash`), and event root hash — but the trailing comment says outright that it **does not** check the state checkpoint hash, hot-state checkpoint hash, or `position_state_checkpoint_hash` fields of `TransactionInfo`: [2](#0-1) 

Callers of this comparator: [3](#0-2) 

I could not find any call site of `ensure_match_transaction_info` that separately verifies these checkpoint hashes elsewhere before treating a replay as successful — it is used specifically to gate whether replay succeeded per-transaction.

### Title
Replay/debugger verification silently ignores state-checkpoint-hash divergence — ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole comparator used by replay-verification tooling (`db-tool replay-on-archive`, `aptos-debugger`, CLI) to confirm that locally re-executed transaction output matches the trusted committed `TransactionInfo`. It checks status, gas, write-set hash, and event-root hash, but knowingly skips comparing the state/hot-state checkpoint hash and the `position_state_checkpoint_hash` fields, as documented in its own inline TODO.

### Finding Description
The function validates only a subset of the fields that make up a committed `TransactionInfo`'s hash-bound identity: [4](#0-3) 

It then returns `Ok(())` without checking the checkpoint-hash fields, with the code comment acknowledging the gap directly: [2](#0-1) 

Because these checkpoint hashes are the fields that bind a `TransactionInfo` to the actual post-transaction state root (including the trading-native "position" state root gated by the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature), a locally-executed VM output whose write-set/events hash correctly but whose resulting state root diverges (e.g., due to a state-application/merkle-construction bug, or feature-flag/versioning mismatch affecting only the state tree) will still pass `ensure_match_transaction_info` and be reported as a successful, verified replay.

### Impact Explanation
This breaks the "committed state differs from correct VM result" invariant for the one place explicitly designed to catch that divergence during replay-verification: divergence in the authenticated position/state checkpoint root would go undetected by `replay_on_archive` and related debugging tools, since the comparator that is supposed to gate "replay succeeded" doesn't check it. This matches the required "Proof And Storage Pivots" criterion that VM outputs and checkpoint hashes must survive executor-to-storage/replay handoff and be independently re-verified, and that Hard-fork-only divergence during replay must be detectable — here it explicitly is not, by the tool's own admission.

### Likelihood Explanation
Likelihood is inherent rather than requiring attacker input: any bug that produces a correct write set/events but a wrong resulting state checkpoint root (state-application bug, hot-state computation bug, or trading-native position-root computation bug under `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) would silently pass this check today. This is a real, currently-shipped gap (not speculative), acknowledged in-repo as a to-do before that feature is enabled.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`-derived checkpoint hashes (state checkpoint hash, hot-state checkpoint hash if present, and `position_state_checkpoint_hash` if present) against the corresponding fields in `txn_info`, at least whenever `version`'s on-chain state supplies those hashes, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`; alternatively, gate any downstream "verified" success reporting from replay/debugger tools on an explicit, separate checkpoint-hash check until this comparator is completed.

### Proof of Concept
Not directly exploitable by an external unprivileged actor against mainnet consensus (this comparator is not on the block-commit critical path — commit correctness there is enforced elsewhere via full `TransactionInfo` equality, e.g. `ensure_transaction_infos_match`). The concrete corrupted-value path is: a `TransactionOutput` whose `write_set`/`events` hash matches `txn_info.state_change_hash()`/`event_root_hash()` but whose *actual resulting state* (as would be captured by the checkpoint hash fields) diverges from `txn_info`'s checkpoint hash — `ensure_match_transaction_info` returns `Ok(())` regardless, as shown by the fields it omits at [2](#0-1) , and is consumed as a pass/fail signal by `storage/db-tool/src/replay_on_archive.rs` at [5](#0-4) .

**Caveat / uncertainty:** I could not fully verify within the available context whether any other code path independently re-validates the checkpoint hashes for the specific replay/debugger tools before they report success (I found no such site), nor could I confirm the current on-chain status of `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (enabled/disabled) which affects whether `position_state_checkpoint_hash` is populated on mainnet today. Given the explicit self-documented gap and its direct relevance to the "proof/checkpoint binding must survive replay" invariant, this is reported as the strongest local analog found, but its real-world severity may currently be bounded by that feature flag's rollout status.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L388-405)
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
```
