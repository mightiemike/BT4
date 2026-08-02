### Title
`ensure_match_transaction_info` skips checkpoint-hash verification, letting `db-tool replay-verify` and the Aptos debugger accept a diverged state/hot-state/position-state root as valid - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-comparison routine used by replay/verification tooling to confirm that a locally re-executed transaction output matches the archived, ledger-committed `TransactionInfo`. It explicitly checks status, gas, write-set hash (`state_change_hash`) and event root hash, but a checked-in `TODO` comment documents that it deliberately skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally computed values.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  validates a `TransactionOutput` against an expected `TransactionInfo` by checking:
- `status` vs. `txn_info.status()`
- `gas_used` vs. `txn_info.gas_used()`
- `CryptoHash::hash(write_set)` vs. `txn_info.state_change_hash()`
- computed `event_root_hash` vs. `txn_info.event_root_hash()`

but the function returns `Ok(())` without ever comparing `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` to anything computed from the replayed execution. The comment at [2](#0-1)  states this directly: "this comparator ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is used as the sole per-transaction integrity gate in two tools:
- `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify`, which calls `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_infos[idx], Some(&expected_writesets[idx]), Some(&expected_events[idx]))` at [3](#0-2)  and treats success as proof the replay matches the archived, signed ledger data.
- `aptos-move/aptos-debugger`'s `print_mismatches`, which calls the same function per output at [4](#0-3) .

Because the Sparse Merkle Tree state-checkpoint root (`state_checkpoint_hash`), the hot-state Merkle root (`hot_state_checkpoint_hash`), and the position/native-trading state root (`position_state_checkpoint_hash`) are exactly the fields that summarize the authenticated post-execution world state, skipping them means these tools' "verification passed" result is not actually proof that the locally computed state root matches the chain's committed root. A local execution divergence that changes only the state tree root (but happens to preserve the write-set BCS hash equality check — e.g., any bug in `state_checkpoint_hash`/hot-state/position-state root computation elsewhere in the executor, a JMT or hot-state Merkle bug, or a hard-fork-only divergence) would pass `ensure_match_transaction_info` silently.

### Impact Explanation
This does not corrupt consensus-committed state directly (validators still gate on the full `TransactionInfo` hash via the accumulator/consensus path), but it breaks the proof-integrity guarantee of the tools whose entire purpose is authenticating that a replayed/re-executed ledger matches the historically committed and signed state roots:
- `db-tool replay_on_archive` is the tool operators/auditors use to confirm archived history replays deterministically to the same committed ledger state; a state-root divergence (including hard-fork-only divergences, one of the explicitly in-scope impact categories) would be silently accepted as "verified", masking state-commitment bugs instead of catching them.
- The Aptos debugger's mismatch-reporting path similarly under-reports divergences limited to checkpoint/hot-state/position-state roots.

Given the explicit acknowledgment in the code that this masks divergence in the "authenticated position state root," and that these are exactly the state-commitment fields called out as in-scope ("Wrong accumulator root ... state proof accepted as valid" and "Hard-fork-only divergence during ... replay"), this is a High-severity proof-integrity gap in the replay-verification path.

### Likelihood Explanation
Likelihood of the checked comparison itself being wrong is not directly exploitable by an external attacker (no adversarial input is needed to trigger it — it is a structural gap in the verifier), but any latent bug in state-checkpoint/hot-state/position-state root computation (several such subsystems are actively under development per the `position_state_checkpoint_hash`/trading-native code paths visible in the same file) would go undetected by anyone relying on `replay_on_archive` or the debugger to validate historical correctness. The gap is unconditionally present (not feature-flagged) and will remain latent until someone manually notices a state fork.

### Recommendation
Extend `ensure_match_transaction_info` to compute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` from the actual locally-produced state (or thread these values in from callers, mirroring how `write_set_hash` and `event_root_hash` are derived) before enabling any feature (e.g. `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that depends on `replay_on_archive`/debugger for correctness assurance. At minimum, `replay_on_archive` and the debugger should fail loudly (or emit an explicit "not verified: checkpoint hash unchecked" warning) rather than reporting unconditional success.

### Proof of Concept
Not independently exploitable as a state-corruption PoC — the flaw is a missing check rather than a wrong write. Demonstration path: run `db-tool replay-on-archive` against an execution engine variant that reproduces byte-identical write sets/events but produces a different `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` (e.g., a JMT/hot-state hashing regression); `Verifier::execute_and_verify` at [5](#0-4)  will report zero failed transactions despite the state root diverging from the archived, signed ledger.

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

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L242-245)
```rust
            txn_output
                .ensure_match_transaction_info(version, txn_info, None, None)
                .unwrap_or_else(|err| println!("{}", err))
        }
```
