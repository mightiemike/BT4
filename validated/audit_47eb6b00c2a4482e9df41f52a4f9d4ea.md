## Title
`ensure_match_transaction_info` skips checkpoint-hash verification, letting `replay_on_archive`/replay-verify tooling certify a replayed ledger whose state/hot-state/position-state root diverges from the authenticated chain - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verification tooling to confirm that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` stored on-chain (i.e., the value bound to the accumulator/ledger-info signature). It checks transaction hash, status, gas, event root hash, and write-set hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself flags with a `TODO(trading-native)` comment.

### Finding Description [1](#0-0) 

`ensure_match_transaction_info` validates `status`, `gas_used`, `write_set` hash (`state_change_hash`), and `event_root_hash` against the supplied `TransactionInfo`, but the comment at lines 2197-2202 states plainly:

> "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is the sole per-transaction correctness gate used by `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify`, which calls it directly on freshly re-executed outputs to decide pass/fail for each transaction during replay verification: [2](#0-1) 

The `TransactionInfo`/`TransactionInfoV1` structure carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as authenticated fields (each is part of the object hashed into the transaction accumulator and thus covered by the ledger-info signature), built in `assemble_transaction_infos`: [3](#0-2) 

However, `ensure_match_transaction_info` — the function that is supposed to prove a re-executed `TransactionOutput` is faithful to that authenticated `TransactionInfo` — never re-derives or compares any of these three checkpoint hashes. Only `state_change_hash` (write-set hash) and `event_root_hash` are checked. This means any divergence in the *state Merkle root*, *hot-state Merkle root*, or the newer *native-position state Merkle root* computed during replay is invisible to this check, even though the write set and events could otherwise appear identical.

### Impact Explanation
The state/hot-state/position-state checkpoint hashes are exactly the state-commitment roots this task's "State-Integrity Gate" cares about: they are Merkle roots authenticated by the ledger info signature, and per-transaction they are meant to bind the transaction's post-execution ledger-state root to consensus. Because `ensure_match_transaction_info` silently omits comparing them:

- `db-tool replay_on_archive` (and any other caller of this method, e.g. `aptos-debugger`'s replay path and `aptos-move/cli`'s replay command) can certify a chain segment as "correctly replayed" while the locally computed state Merkle root, hot-state root, or the new native-position state root actually differs from the one committed and signed by validators.
- This directly matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" impact category and "Authenticated API or state-view output bound to the wrong version, object, or proof context," since verification tooling is a load-bearing integrity check for detecting state divergence (e.g., after a JMT/SMT bug, an aggregator/delayed-field bug, or a bug in the newly introduced native-position pipeline) but will pass silently.
- Because the comment explicitly calls out `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/position-state roots as the primary blind spot, any bug in the new native-position Merkle commitment path (`do_state_checkpoint.rs`, `PositionStateWithSummary`, etc.) would go undetected by the standard replay-verification tool used to audit archive nodes and backups.

### Likelihood Explanation
This is not a hypothetical: the gap is unprivileged (any replay-verify run, any operator running `db-tool replay-on-archive`, triggers this code path) and is explicitly acknowledged in-repo as an open, unresolved TODO rather than a defense-in-depth omission. It requires no attacker action beyond an actual state-computation divergence (bug elsewhere) existing; when one occurs, this check is the mechanism meant to detect it and currently cannot. Given `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/position-state is actively being developed (new pipeline, `do_state_checkpoint.rs`, `ProvablePositionStateSummary`, etc., all touched recently in this codebase), a real risk of state-root divergence during rollout is non-trivial, and this validator is precisely the safety net that should catch it but doesn't.

### Recommendation
Extend `ensure_match_transaction_info` to also assert equality of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on the given `TransactionInfo` and enabled features) against locally computed/tracked checkpoint hashes from the transaction output/execution pipeline, mirroring the treatment already given to `write_set`/`state_change_hash` and `event_root_hash`. Do this before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` broadly, as the comment itself recommends.

### Proof of Concept
Not directly exploitable by an external attacker; this is a verification-logic gap. Demonstration path: run `db-tool replay-on-archive` (or any caller of `ensure_match_transaction_info`) over a version range where the locally recomputed state/hot-state/position-state Merkle root differs from the archived `TransactionInfo`'s checkpoint hash (e.g. by injecting a divergent state value via a state-view or storage bug in a test harness) while keeping the write set, events, gas, and status identical — `execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs` will report success (`Ok(None)`) despite the state root mismatch, because `ensure_match_transaction_info` never compares checkpoint hashes. [4](#0-3)

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L95-121)
```rust
                let txn_info = if transaction_info_v1 {
                    TransactionInfo::builder_v1()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .maybe_hot_state_checkpoint_hash(
                            hot_state_checkpoint_hashes.and_then(|hot| hot[i]),
                        )
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .maybe_position_state_checkpoint_hash(
                            position_state_checkpoint_hashes.and_then(|p| p[i]),
                        )
                        .build()
                } else {
                    TransactionInfo::builder_v0()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .build()
                };
```
