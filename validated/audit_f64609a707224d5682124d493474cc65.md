Found it. There's a self-documented gap in `TransactionOutput::ensure_match_transaction_info`.

### Title
`ensure_match_transaction_info` omits state-checkpoint hash validation, letting replay-verify accept a diverged trading-native/hot-state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative "did local re-execution reproduce the authenticated `TransactionInfo`" check used by chunk replay-verify (`execution/executor/src/chunk_executor/mod.rs`) and the `db-tool` replay-on-archive verifier (`storage/db-tool/src/replay_on_archive.rs`). It only compares status, gas, write-set hash (`state_change_hash`) and event root hash. It never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or the newer `position_state_checkpoint_hash` fields that are also part of the committed, hashed `TransactionInfo`.

### Finding Description
`assemble_transaction_infos` in `execution/executor/src/workflow/do_ledger_update.rs` builds `TransactionInfo::V1` with `maybe_state_checkpoint_hash`, `maybe_hot_state_checkpoint_hash`, and `maybe_position_state_checkpoint_hash` — all of which are part of the struct that gets `CryptoHash::hash()`ed into the transaction accumulator leaf (`types/src/transaction/mod.rs:2261-2284`). These fields authenticate the JMT/hot-state/position (trading-native) state roots at checkpoint boundaries.

However `TransactionOutput::ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) — the function chunk-executor and `replay_on_archive` use to decide whether locally re-executed output matches the trusted, backed-up `TransactionInfo` — never checks these checkpoint-hash fields: [1](#0-0) 

The code itself contains a `TODO(trading-native)` comment admitting this: [2](#0-1) 

This is used by `ChunkExecutor::verify_execution` in the state-sync/replay path: [3](#0-2) 

and by the standalone replay-verify tool that auditors/operators run against backup archives to detect divergence from the authenticated chain history: [4](#0-3) 

Because the checkpoint hashes are skipped, if a local VM/storage bug (or a future bug introduced by the "trading-native" position-state feature currently gated behind `compute_trading_native_state_roots`) produces a different state-checkpoint root, hot-state checkpoint root, or position-state checkpoint root than what is recorded in the backup/authenticated `TransactionInfo`, both `verify_execution` (used during real chunk-based state sync) and the offline `replay_on_archive` verifier will report success. The write-set hash and event hash can match while the actual committed JMT/hot-state/position-state root has silently diverged from the canonical chain, since those two hashes do not cover checkpoint state roots.

### Impact Explanation
This breaks the core state-integrity guarantee that "committed/replayed state must match the authenticated chain, and replay-verify must detect divergence." A node performing chunk-based state sync (which calls `verify_execution`) or an operator running `db-tool replay-on-archive` to audit archive integrity can accept and commit a state root that differs from the one certified by validator signatures on the `TransactionInfo`/accumulator, without any error being raised. This is exactly the "wrong accumulator root ... accepted as valid" and "replay path... must not reinterpret committed data into a different ledger state" class of impact called out in the state-integrity gate, since the checkpoint-hash fields are precisely the commitment to state roots in the ledger's proof structure.

### Likelihood Explanation
The gap requires an independent divergence to already exist (e.g. a bug in state-checkpoint/hot-state/position-state root computation) — `ensure_match_transaction_info` doesn't itself cause corruption, but it removes the safety net meant to catch it, and it's already partially "live" today for the `state_checkpoint_hash`/`hot_state_checkpoint_hash` fields generally (not just the yet-unreleased trading-native feature), since the function checks none of the three checkpoint hashes for any transaction, not only ones with `compute_trading_native_state_roots` enabled. This makes the missing checks a real, present-day masking of any state-checkpoint-root bug during replay verification, not merely a future risk.

### Recommendation
- Short term: extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in `txn_info`) against the locally computed checkpoint hashes for the corresponding transaction (recomputed the same way `DoStateCheckpoint`/`do_ledger_update.rs` does), failing loudly on mismatch just like the other fields.
- Long term: before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in production, ensure all replay/verify code paths (`chunk_executor`, `db-tool replay-on-archive`, and any future "verify_execution"-style checks) validate every field encoded in `TransactionInfo` that participates in the chain's cryptographic commitment, not a subset.

### Proof of Concept
1. Introduce (hypothetically, or via an existing latent bug in position/hot-state root computation) a discrepancy between the locally computed state-checkpoint/hot-state/position-state root and the one recorded in a `TransactionInfo` fetched from backup/state-sync peer, while keeping the write set and events identical.
2. Run `ChunkExecutor::verify_execution` (state sync) or `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` against this chunk.
3. Observe `ensure_match_transaction_info` returns `Ok(())` because it only checks status, gas, write-set hash, and event root hash — the checkpoint-hash mismatch is never inspected, so the divergent state is silently accepted as "verified."

### Citations

**File:** types/src/transaction/mod.rs (L2159-2196)
```rust
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

```

**File:** types/src/transaction/mod.rs (L2197-2203)
```rust
        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
```

**File:** execution/executor/src/chunk_executor/mod.rs (L685-706)
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
