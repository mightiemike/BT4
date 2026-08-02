I have enough to finalize the analog. The strongest, self-documented candidate is the `state_change_hash`/checkpoint-hash gap in `TransactionOutput::ensure_match_transaction_info`, which is the exact authenticated-invariant comparator used by replay/verification tooling — directly analogous to the ETH bug's core theme ("the value computed by comparator diverges from the actually-committed authenticated value, and it goes undetected").

### Title
Replay-verification comparator omits state/hot-state/position checkpoint-root validation, allowing an undetected state-root divergence to be reported as a verified replay - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-invariant check used by `db-tool`'s `replay_on_archive` (and the chunk executor's `verify_execution` path) to confirm that a locally re-executed `TransactionOutput` matches the trusted, accumulator-committed `TransactionInfo` for a given version. It validates status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that are the authenticated Merkle roots of the actual ledger state at that version. The code contains an acknowledged `TODO(trading-native)` admitting this gap.

### Finding Description [1](#0-0) 

`ensure_match_transaction_info` is the function that binds a freshly-computed `TransactionOutput` (from local VM re-execution) to the trusted `TransactionInfo` retrieved either from a verified `TransactionInfoWithProof`/accumulator (in `db-tool/replay_on_archive.rs`) or from `TransactionInfo` read directly out of storage during chunk replay (`execution/executor/src/chunk_executor/mod.rs::verify_execution`). It checks:
- `status` vs `expected_txn_status`
- `gas_used`
- `write_set_hash` (`CryptoHash::hash(self.write_set())`) vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

It never checks `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` against anything computed from the locally re-executed state. These checkpoint hashes are the actual Jellyfish-Merkle roots that authenticate the full account/resource state at that version (as opposed to only the write-set diff hash, which only covers per-transaction deltas and does not catch errors in how those deltas are folded into the global state tree — e.g. bugs in `update_with_db_reader`, `StateMerkleBatchCommitter`, hot-state promotion logic, or the native-position sharded JMT path in `execution/executor/src/workflow/do_state_checkpoint.rs`).

This is called out directly in the code: [2](#0-1) 

The comment states this "ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution," and instructs that checkpoint hashes must be validated "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`."

Both call sites treat a clean return from this function as full confirmation of a correct replay: [3](#0-2) [4](#0-3) 

Since `COMPUTE_TRADING_NATIVE_STATE_ROOTS` gates whether the position-state root is computed from execution at all (`types/src/block_executor/config.rs` lines 184-187), any bug in the state-checkpoint-hash computation path (main state, hot state, or native-position state) that produces a wrong root but a correct write-set/event/gas/status tuple will pass `ensure_match_transaction_info` silently. This is the direct Aptos analog of the reported issue's class: a downstream consumer (data-column/proposer verifier in the source report; replay-verify tooling here) computes/uses one value (write-set hash) as a stand-in for another value (the full committed state root) that can legitimately diverge, and the mismatch is never detected by the code path that is supposed to catch it.

### Impact Explanation
This breaks the state-integrity invariant that replay/verification tooling must independently reconstruct and validate the authenticated ledger state root, not merely the write-set diff. A corrupted or incorrectly-computed state-checkpoint root (main state, hot state, or the newer native-position state) can be silently accepted as "verified" replay by `replay_on_archive` and by the chunk executor's execution-verification mode, undermining the primary safety mechanism relied on for validating archived/backed-up history and detecting divergence before enabling features like `COMPUTE_TRADING_NATIVE_STATE_ROOTS`. This matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "wrong accumulator root ... proof ... accepted as valid" categories in the state-integrity gate.

### Likelihood Explanation
This is a real, currently-active gap acknowledged by the developers (not a hypothetical): the comparator is invoked in both production tooling paths (`replay_on_archive.rs` and `chunk_executor`'s `verify_execution`), and any latent bug in state-checkpoint-hash computation (of which this codebase has several complex, newly-added paths — hot state, native-position sharded JMT, `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) would go undetected by this specific safety net. It requires an underlying root-computation bug to actually trigger a divergence, so likelihood of exploitation is tied to the correctness of those other complex paths, but the detection gap itself is unconditionally present today.

### Recommendation
Extend `ensure_match_transaction_info` to independently recompute (or accept as a parameter) the expected `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` for the locally-executed state and compare them against `txn_info`'s corresponding fields whenever those fields are `Some`, before returning `Ok(())`. Update both call sites (`db-tool/replay_on_archive.rs`, `execution/executor/src/chunk_executor/mod.rs::verify_execution`) to supply the recomputed checkpoint hash for versions that carry one, and gate `COMPUTE_TRADING_NATIVE_STATE_ROOTS` rollout on this check being enforced, per the existing TODO.

### Proof of Concept
Not independently reproducible as a standalone PoC without an underlying state-root computation bug; the finding is that the detection/verification comparator itself has a structural gap. To demonstrate: instrument `execution/executor/src/workflow/do_state_checkpoint.rs` (or the hot/position state summary paths) to deliberately compute a wrong `state_checkpoint_hash` (or `position_state_checkpoint_hash`) while leaving the write set, events, gas, and status correct; run `db-tool replay-on-archive` over that range — `execute_and_verify` (storage/db-tool/src/replay_on_archive.rs:392-405) will report success because `ensure_match_transaction_info` never inspects the checkpoint-hash fields.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L685-708)
```rust
        for (version, txn_out, txn_info, write_set, events) in multizip((
            begin_version..end_version,
            &execution_output.to_commit.transaction_outputs,
            transaction_infos.iter(),
            write_sets.iter(),
            event_vecs.iter(),
        )) {
            if let Err(err) = txn_out.ensure_match_transaction_info(
                version,
                txn_info,
                Some(write_set),
                Some(events),
            ) {
                return if verify_execution_mode.is_lazy_quit() {
                    error!("(Not quitting right away.) {}", err);
                    verify_execution_mode.mark_seen_error();
                    Ok(version + 1)
                } else {
                    Err(err)
                };
            }
        }
        Ok(end_version)
    }
```
