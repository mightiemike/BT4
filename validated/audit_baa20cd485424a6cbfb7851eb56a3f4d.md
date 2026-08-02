### Title
`ensure_match_transaction_info` never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, so replay/db-tool verification silently accepts a corrupted state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity check used by replay/debug tooling to confirm that a locally re-executed transaction produced the same result as the transaction that was originally committed and authenticated in the archived ledger (`TransactionInfo`). The function validates status, gas used, write-set hash, and event root hash, but it explicitly skips validating the state checkpoint hash (JMT state root), the hot-state checkpoint hash, and the position (trading-native) state checkpoint hash against the recomputed local values [1](#0-0) .

### Finding Description
`ensure_match_transaction_info` compares only four fields between the locally-recomputed `TransactionOutput` and the archived/expected `TransactionInfo`: transaction status, gas used, the write-set hash (`state_change_hash`), and the event root hash [2](#0-1) . It never compares `txn_info.state_checkpoint_hash()` (the JMT state root captured at checkpoint boundaries) nor the hot-state / position-state checkpoint hashes, and the code's own comment acknowledges this gap:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [3](#0-2) 

This function is the sole correctness gate used by `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify`, which re-executes historical transactions from an archive via `AptosVMBlockExecutor` and calls `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_infos[idx], Some(&expected_writesets[idx]), Some(&expected_events[idx]))` to decide whether the replay matches the canonical, signed ledger history [4](#0-3) . Because `state_checkpoint_hash` (and the hot/position variants) is never checked here, any divergence in the actual JMT state root — e.g. caused by a nondeterministic bug in state-tree update logic, hot-state accumulation, or the sharded/native position-state tree introduced in this codebase (`storage/aptosdb/src/native_state_committer.rs`, `execution/executor/src/workflow/do_state_checkpoint.rs`) — will not be detected by this tool even though the write set and events match. `TransactionInfo::state_checkpoint_hash` is precisely the field the accumulator commits and that light clients/validators rely on as the authenticated root of state at that version, so silently ignoring it here breaks the "replay/verification must catch any commitment divergence" invariant.

The same code path is also reachable from `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs`, both of which are used operationally to validate historical execution correctness.

### Impact Explanation
`replay-verify` / `replay_on_archive` is a security control specifically designed to catch determinism bugs and state-corruption bugs before/after they reach mainnet, by replaying the authenticated transaction history and diffing it against the recomputed result. If the state root check is skipped, a bug elsewhere in this fork's storage/execution pipeline (e.g., in the newly introduced position/trading-native state tree, hot-state checkpointing, or JMT commit paths) that corrupts the state root while leaving the write set byte-identical would pass replay-verification as "successful", giving false assurance that the chain's state is consistent. This is a hard-fork-class detection failure: exactly the kind of divergence the tool exists to catch (state root mismatch = fork condition) is the one field it does not check. This satisfies the "hard-fork-only divergence during commit, replay, restore, or proof verification" impact category.

### Likelihood Explanation
The gap is deterministic and always present — it doesn't depend on an attacker; it triggers whenever there is any state-root divergence (write set correct, JMT root wrong) between the archived commitment and a locally recomputed one, which is realistic given this codebase's several state-commitment subsystems (hot state, position/trading-native state, sharded state-merkle DB) are new and much more complex than the write-set/event path already covered. The code's own TODO comment confirms the maintainers are aware the gap exists but it remains unaddressed and is already gating the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature rollout.

### Recommendation
Extend `ensure_match_transaction_info` to also assert that the locally-recomputed `state_checkpoint_hash` (and, when applicable, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) equals the value in `txn_info`, mirroring the existing `write_set_hash` / `event_root_hash` checks, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or relying on `replay_on_archive` results as a correctness signal.

### Proof of Concept
1. In `types/src/transaction/mod.rs`, note `ensure_match_transaction_info` only checks `status`, `gas_used`, `write_set_hash`, and `event_root_hash` [2](#0-1) ; no comparison of `txn_info.state_checkpoint_hash()` exists anywhere in the function body.
2. `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` executes a chunk of historical transactions and calls this method as the pass/fail criterion for the replay [4](#0-3) .
3. Construct (or simulate) a scenario where a bug in state-checkpoint computation (e.g., in `execution/executor/src/workflow/do_state_checkpoint.rs`'s position/hot-state root logic) produces a wrong `state_checkpoint_hash`/`position_state_checkpoint_hash` while the write set and events remain identical to the archived transaction. Running `replay_on_archive` over this range reports success (no error returned), even though the state root has diverged from the authenticated ledger — demonstrating the verification tool fails to detect committed-state corruption.

Note: I could not execute this end-to-end (no sandbox/build access here) to observe the tool's literal stdout; the finding is based on direct code inspection of the comparator and its caller, and is corroborated by the maintainers' own inline TODO acknowledging the exact gap.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L392-406)
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
        }
```
