### Title
`ensure_match_transaction_info` skips checkpoint-hash validation, allowing replay-verify/chunk-executor to accept a divergent state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity check used by replay/verification tooling to confirm a locally re-executed transaction output matches the authenticated `TransactionInfo` read from storage (an already-committed, signed ledger record). The function explicitly compares status, gas used, write-set hash (`state_change_hash`), and event root hash, but — per its own inline TODO — deliberately skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the freshly computed values. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` validates a subset of the fields inside `TransactionInfo` (the accumulator leaf that is cryptographically bound to the signed `LedgerInfo`):

- `status`
- `gas_used`
- `state_change_hash` (write-set hash)
- `event_root_hash` [2](#0-1) 

It never recomputes or compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, even though these are part of the same `TransactionInfoV0`/`TransactionInfoV1` structure that is hashed and stored in the transaction accumulator that ultimately backs the validator-signed ledger root. The function's own comment concedes the gap: [3](#0-2) 

This function is the sole per-transaction integrity gate in the archive replay-verification tool: `ReplayController::execute_and_verify` re-executes transactions from the VM and calls `ensure_match_transaction_info` against the `expected_txn_infos` fetched from the target DB, treating a return of `Ok(())` as proof the replay matches the historical record. [4](#0-3) 

Because `state_checkpoint_hash` (the Sparse-Merkle world-state root for the transaction/block) and `position_state_checkpoint_hash` (used by the new "trading-native" position state tree, referenced together with `hot_state_checkpoint_hash`) are excluded from comparison, a re-executed transaction whose write-set hash and event hash happen to match, but whose resulting state-checkpoint or position-state Merkle root diverges from the authenticated on-chain root, will be reported as a **successful, verified replay**. The same helper is also invoked from `execution/executor/src/chunk_executor/mod.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs`, so the same blind spot propagates to state-sync's chunk executor and to CLI-driven local checks. [5](#0-4) 

The `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` fields are produced by `do_ledger_update.rs`'s `assemble_transaction_infos` and by `do_state_checkpoint.rs`, and are exactly the fields intentionally omitted from the equality check. [6](#0-5) 

### Impact Explanation
This is a state-commitment/proof-integrity gap rather than a fund-loss bug: the tooling that is supposed to authenticate replayed history against the immutable, validator-signed accumulator can be made to (or can silently) accept a transaction whose derived world-state root diverges from the real chain state, as long as its write-set and event hashes still match. This directly undermines the "committed state that differs from the correct VM result... accepted as valid" and "authenticated ... output bound to the wrong version... root" integrity gates: a corrupted/incorrect state-checkpoint root produced by a bug elsewhere (e.g., in a future feature gated behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, or by a bug in `do_state_checkpoint`/position-state summary logic) would not be caught by `replay_on_archive`/chunk-executor verification, undermining confidence in disaster-recovery/replay-verify tooling that operators and auditors rely on to detect ledger divergence, including hard-fork-only divergence scenarios during restore/replay.

### Likelihood Explanation
The gap is unconditional and always present in the current code (it is not gated behind the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag — the flag only controls when the new position-state root computation is *enabled*, but the comparator omission already exists today). Any divergence-causing bug in state-checkpoint/JMT computation, hot-state summary, or the in-development position-state tree would go undetected by this specific check today, and would remain undetected after the feature launches unless the TODO is resolved before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is turned on. I was not able to fully trace whether some other independent path (e.g., accumulator-root comparison at a higher layer in `replay_on_archive.rs`) provides a redundant catch of state-checkpoint-hash mismatches; the `replay_on_archive.rs` file content around lines 90–210 was not fully visible in this session, so it is possible (but not confirmed) that a coarser accumulator-hash check exists elsewhere in that file providing partial mitigation for the archive-replay path specifically. The chunk-executor and debugger/CLI call sites, however, rely on `ensure_match_transaction_info` directly for this purpose per the citations above.

### Recommendation
Extend `ensure_match_transaction_info` to recompute the state-checkpoint hash (and, once available, `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) from the locally re-executed state and assert equality against `txn_info.state_checkpoint_hash()` / the corresponding accessors, matching the pattern already used for `state_change_hash` and `event_root_hash`. This must land before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, as flagged by the existing TODO.

### Proof of Concept
Conceptual PoC (cannot be executed without full repo access):
1. Run `storage/db-tool/src/replay_on_archive.rs` (`ReplayController::verify`) against a DB range containing a transaction.
2. Introduce a state-checkpoint root divergence for that transaction — e.g., a state value that the VM/executor computes identically in bytes for the write set (matching `state_change_hash`) but that produces a different Sparse Merkle Tree root during checkpoint calculation (a class of bug in `do_state_checkpoint.rs` root computation, independent of the write-set content itself), or simulate a future position-state-tree computation bug once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is active.
3. Observe that `execute_and_verify` in `replay_on_archive.rs` calls `ensure_match_transaction_info`, which returns `Ok(())` despite the state_checkpoint_hash/position_state_checkpoint_hash mismatch, because those fields are never compared.
4. The replay is reported as fully verified even though the authenticated ledger's state-checkpoint root and the locally recomputed root differ — a state-commitment divergence going undetected by the very tool designed to catch it. [1](#0-0) [4](#0-3)

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

**File:** execution/executor/src/chunk_executor/mod.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
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
