## Title
Missing checkpoint-hash validation in `ensure_match_transaction_info` allows replay-verify to accept a wrong state-checkpoint root as valid — (`types/src/transaction/mod.rs`)

### Summary
The external report's bug class is: a settings-update function silently ignores one field (`migration_token_allocation`) from its input, so that field is never validated/applied even though the surrounding logic implies it should be. The Aptos-native analog is `TransactionOutput::ensure_match_transaction_info` in [1](#0-0) , the function used by replay/verification tooling to confirm that a freshly re-executed `TransactionOutput` matches an authenticated, previously-committed `TransactionInfo`. It checks status, gas, write-set hash, and event-root hash, but explicitly skips the state-checkpoint / hot-state-checkpoint / position-state-checkpoint hash fields, as acknowledged by its own `TODO(trading-native)` comment.

### Finding Description
`ensure_match_transaction_info` is the authoritative comparator used to assert that a locally re-executed transaction output is consistent with the `TransactionInfo` recorded in a signed/backed-up ledger segment. It validates:
- execution status [2](#0-1) 
- gas used [3](#0-2) 
- write-set hash against `state_change_hash` [4](#0-3) 
- event-root hash [5](#0-4) 

but it never compares `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` against anything computed from the freshly executed output. The comment in the code itself documents this: "this comparator ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution." [6](#0-5) 

This function is the sole correctness gate used by `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes archived transactions and calls `ensure_match_transaction_info` to decide pass/fail: [7](#0-6) . Since checkpoint hashes are excluded from the comparison, any divergence between the locally computed state/hot-state/position-state checkpoint root (e.g. `TRANSACTION_INFO_V1`/`HOT_STATE_ROOT_IN_TXN_INFO`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS` paths in `DoLedgerUpdate::assemble_transaction_infos`, see [8](#0-7) ) and the archived, ledger-committed `TransactionInfo` will not be flagged. The same function is also used by `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs` for the equivalent "verify local execution against on-chain ground truth" flows.

### Impact Explanation
This breaks the "Wrong accumulator root, Merkle proof, transaction proof, event proof, or state proof accepted as valid" invariant from the state-integrity gate: a `TransactionInfo`/state-checkpoint-hash mismatch is a proof-bearing field bound to the ledger's version/root, yet the primary tool designed to catch such divergences treats it as immaterial. A latent executor/state-checkpoint bug (e.g., in hot-state or "trading-native" position-state root computation) that produces the wrong checkpoint hash would go completely undetected by replay-verify, giving false assurance that historical execution is correct even though the actual committed Merkle/JMT state root diverges from the correct VM result. This is exactly the kind of hard-fork-relevant, proof-verification blind spot the gate targets.

### Likelihood Explanation
The gap is real and currently reachable in-tree (not hypothetical): it's exercised whenever `replay_on_archive`, `aptos-debugger`, or the CLI replay/verify commands run against archived data with a checkpoint-hash-computing feature enabled (state checkpoint hashing, hot-state, or the not-yet-fully-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/position-state path). The code's own comment confirms this is a known, currently-unaddressed gap gating the safe rollout of `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

### Recommendation
Extend `ensure_match_transaction_info` to compute and compare the state-checkpoint hash (and, where applicable, the hot-state and position-state checkpoint hashes) of the locally executed output against the corresponding fields on `txn_info`, mirroring the pattern already used for `state_change_hash` and `event_root_hash`. Add regression tests (analogous to `test_transaction_and_output_list_with_proof` in `types/src/proof/unit_tests/proof_test.rs`) asserting that a checkpoint-hash mismatch causes `ensure_match_transaction_info`/replay-verify to fail before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

### Proof of Concept
1. Archive/backup a range of transactions whose committed `TransactionInfo` carries a specific `state_checkpoint_hash` (or hot/position checkpoint hash).
2. Introduce (or simulate via a modified executor) a divergent checkpoint-hash computation locally — e.g., mutate `DoStateCheckpoint`'s output hashes before they reach `assemble_transaction_infos` — while keeping the write-set, events, gas, and status identical to the recorded `TransactionInfo`.
3. Run `storage/db-tool/src/replay_on_archive.rs`'s `verify()`, which calls `ensure_match_transaction_info` at [9](#0-8) .
4. Observe that `Ok(())` is returned and no error is reported for the divergent checkpoint hash, because none of the checkpoint-hash fields are compared in `ensure_match_transaction_info` ( [6](#0-5) ), demonstrating replay-verify silently accepts a state-root divergence as a match.

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
