### Title
`ensure_match_transaction_info` never validates state/hot-state/position checkpoint hashes, letting replay-verify accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single correctness gate used by mainnet archive/replay-verification tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger`) to prove that a locally re-executed transaction matches the transaction info recorded in a backup/archive. The function checks status, gas, write-set hash (`state_change_hash`), and event root hash, but explicitly skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the fields that authenticate the resulting state (Merkle/JMT) root after a checkpoint transaction.

### Finding Description [1](#0-0) 

The comparator computes and checks:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `CryptoHash::hash(write_set)` vs `txn_info.state_change_hash()`
- event accumulator root vs `txn_info.event_root_hash()`

It never computes or checks the state-checkpoint hash that should follow from applying `write_set` to the parent state. This gap is acknowledged directly in the code: [2](#0-1) 

The TODO comment explicitly says: "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This is not a hypothetical: `ensure_match_transaction_info` is the *only* verification step performed per-transaction by the replay-verify controller: [3](#0-2) 

Because `expected_writesets[idx]` and `expected_txn_infos[idx]` come straight from the (untrusted) backup/archive being verified, and only `write_set` hash / events / gas / status are validated, a backup file whose `TransactionInfo.state_checkpoint_hash` (or `position_state_checkpoint_hash`) was corrupted or forged to a different value than what the write set actually produces will still pass `execute_and_verify` as long as the write set and events themselves are internally self-consistent with the (also potentially altered) `state_change_hash`. More importantly, a genuine divergence between the executor's freshly computed state root and the archived/committed root (e.g. from a JMT/hot-state bug, or a maliciously/accidentally corrupted state-summary component reaching consensus in the "trading-native"/position-state feature path) would go completely undetected by replay-verify, since that tool never recomputes or compares the state-checkpoint root at all.

### Impact Explanation
Replay-verify and the debugger's `execute_and_verify`-style checks are the tooling operators rely on to detect state divergence — including hard-fork-class bugs where the true world-state Merkle root computed at commit time does not match what's authenticated in the backup/archive. Since `ensure_match_transaction_info` silently skips the state/hot-state/position checkpoint hash fields, this class of divergence is invisible to the tool: an archive or execution path that produces a wrong state root can be certified as "verified" even though the durable ledger state is corrupted relative to the correct VM result. This falls squarely into the specified in-scope impact: "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Committed state that differs from the correct VM result... accepted as valid," with high severity because it defeats the primary safety net for detecting exactly this class of bug on mainnet-derived backups.

### Likelihood Explanation
The gap is unconditional and always present for any archive/replay-verify run and any debugger replay that calls this exact method (only gas/status/write-set/event hash are checked); it requires no attacker action beyond having (or producing) an archive/backup whose declared checkpoint hash does not match the true resulting state, or simply having a legitimate divergence bug elsewhere in state-root computation (e.g., a bug in `DoStateCheckpoint`/`ProvableStateSummary`/position-state root logic) which this exact check is supposed to catch but does not. The comment itself flags it as a known, currently-unmitigated pre-condition for enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, confirming the developers consider this an active, unresolved gap rather than a theoretical one.

### Recommendation
Extend `ensure_match_transaction_info` to independently recompute the state-checkpoint hash (and hot-state / position-state checkpoint hash when applicable) from execution output and compare it against `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()` whenever those fields are present, failing verification on mismatch — mirroring the existing `state_change_hash`/`event_root_hash` checks. This must be done before `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/position-state roots are enabled, and should not be deferred, since replay-verify tooling is currently the only place expected to catch this class of ledger-state divergence.

### Proof of Concept
Conceptual PoC (cannot be executed without live tooling access):
1. Take a legitimate transaction backup manifest/chunk consumed by `TransactionRestoreBatchController`/`replay_on_archive`.
2. Leave `write_set`, `events`, `gas_used`, and `status` untouched (so `state_change_hash` and `event_root_hash` still validate), but alter `TransactionInfo.state_checkpoint_hash` (or `position_state_checkpoint_hash`) to an arbitrary/incorrect value, simulating either a corrupted archive or a genuine state-root computation bug at commit time.
3. Run `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::verify` (or the debugger's equivalent execute-and-compare path) against this manifest.
4. Observe that `execute_and_verify` → `ensure_match_transaction_info` at [4](#0-3)  returns `Ok(())` and the tool reports the range as successfully replayed/verified, despite the state-checkpoint root being wrong — demonstrating that replay-verify cannot detect this specific class of ledger-state corruption.

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
