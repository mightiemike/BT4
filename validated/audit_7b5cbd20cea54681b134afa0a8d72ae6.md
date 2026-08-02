## Finding [1](#0-0) 

### Title
Replay-verify's `ensure_match_transaction_info` skips validating `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`, letting a diverging state root pass as verified — (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole invariant check used by `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify` (and by `aptos-debugger`/`cli`) to certify that locally re-executing a mainnet backup produces the *same* ledger state as the authenticated backup's `TransactionInfo`. It validates `status`, `gas_used`, the write-set hash (`state_change_hash`), and the event root hash, but it never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the authenticated `TransactionInfo`. This mirrors the reported bug class: one side of a two-part invariant (write set) is checked, the other side (state/account Merkle root) is silently skipped, so a divergent output can be accepted.

### Finding Description
`ensure_match_transaction_info` compares only these fields between the locally-computed `TransactionOutput` and the archived `TransactionInfo`:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `CryptoHash::hash(self.write_set())` vs `txn_info.state_change_hash()`
- computed event-accumulator root vs `txn_info.event_root_hash()`

It never touches `txn_info.state_checkpoint_hash()` (the Sparse-Merkle-Tree / Jellyfish-Merkle root of world state at checkpoint boundaries), nor `hot_state_checkpoint_hash`, nor `position_state_checkpoint_hash`. This is explicitly acknowledged by the in-code TODO: [2](#0-1) 

`replay_on_archive.rs`'s `Verifier::execute_and_verify` uses exactly this comparator as its pass/fail gate when replaying an authenticated backup archive against fresh VM execution: [3](#0-2) 

Because `state_change_hash` only authenticates the *write set produced by this transaction*, it does not by itself prove that applying that write set on top of the correct base state produces the correct state root. The state root (`state_checkpoint_hash`, computed in `DoStateCheckpoint::run` via `LedgerStateSummary::update`) is a distinct, independently-computed value derived from accumulating write sets into the Merkle/Jellyfish state tree across a chunk: [4](#0-3) 

A bug anywhere in that state-tree accumulation path (JMT/SMT construction, hot-state tracking, or — as the TODO calls out — the new "trading native" position-state tree gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) can produce a write-set hash that matches (same bytes written) while the resulting Merkle root diverges from the authenticated backup's root. `ensure_match_transaction_info` would still report success, because it never inspects `state_checkpoint_hash`/`position_state_checkpoint_hash`. Replay-verify is the primary tool relied on to certify that mainnet history replays to the historically-committed, authenticated state; skipping the state-root comparison breaks that guarantee for exactly the class of bugs replay-verify exists to catch (post-execution state materialization/commit bugs, not VM output bugs).

### Impact Explanation
This is a proof/verification-integrity gap rather than a live consensus bug: replay-verify (and any debugger/CLI code path reusing `ensure_match_transaction_info`) can certify a backup/replay as "matching" even when the actual state root at a checkpoint boundary has diverged from the authenticated, signed `LedgerInfo`-rooted `TransactionInfo`. This directly falls under the State-Integrity Gate's "Wrong accumulator root ... or state proof accepted as valid" and "Authenticated API or state-view output bound to the wrong version, object, or proof context" — the tool whose entire purpose is to bind local re-computed state to the authenticated version/root silently omits that binding for the state-root fields. Any real divergence in state materialization (e.g., from a hot-state or position-state accumulation defect) would go undetected by replay-verify, undermining confidence in backup/restore integrity checks and any operational decisions (e.g., accepting a snapshot/backup as verified) based on it.

### Likelihood Explanation
The gap is unconditional and always present for standard `state_checkpoint_hash`/`hot_state_checkpoint_hash` (they are simply never compared, regardless of feature flags). The `position_state_checkpoint_hash` piece is explicitly gated behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, which is defined in `aptos_features.rs` and referenced by `aptosdb_reader.rs`/`aptosdb_writer.rs`, indicating active feature-flag plumbing for this new state root; the code's own TODO says this must be fixed "before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS," confirming maintainers are aware the gap becomes materially exploitable once that feature ships. No malicious/privileged actor is required to trigger the gap — it only requires a legitimate state-computation divergence (bug or non-determinism) to go unnoticed by the verification tool.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` between the locally-computed output/state-checkpoint result and the corresponding fields on the authenticated `TransactionInfo`, at least whenever the transaction is a checkpoint boundary (`txn_info.state_checkpoint_hash().is_some()`), before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any state-root-bearing feature) is enabled on mainnet. This closes the verification gap and makes replay-verify a true end-to-end check of committed state, not just of write-set contents.

### Proof of Concept
1. In `replay_on_archive.rs::Verifier::execute_and_verify`, feed a fabricated/corrupted `expected_txn_infos[idx]` whose `state_change_hash`, `status`, `gas_used`, and event root all match the locally executed output's actual write set/events, but whose (attacker- or bug-controlled) `state_checkpoint_hash`/`position_state_checkpoint_hash` differs from what `DoStateCheckpoint` would actually compute for that state.
2. Call `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_infos[idx], Some(&expected_writesets[idx]), Some(&expected_events[idx]))`.
3. Observe `Ok(())` is returned because [1](#0-0)  never inspects the state-checkpoint hash fields — despite the state Merkle root not matching the authenticated `TransactionInfo`, demonstrating that replay-verify would silently pass a state-root divergence.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L36-49)
```rust
        let state_summary = parent_state_summary.update(
            persisted_state_summary,
            &execution_output.hot_state_updates,
            execution_output.to_commit.state_update_refs(),
        )?;

        let last_checkpoint = state_summary.last_checkpoint();

        let state_checkpoint_hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_state_checkpoints,
            last_checkpoint.root_hash(),
            "state",
        )?;
```
