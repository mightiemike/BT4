Found a genuine analog. It's explicitly acknowledged in a code comment as an unfinished validation gap.

### Title
`verify_execution`/`replay_on_archive` chunk-replay verification silently ignores the position/state checkpoint hashes, letting a divergent authenticated state root pass as a verified replay - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info()` — the function used by chunk-executor replay-verification (`execution/executor/src/chunk_executor/mod.rs::verify_execution`) and by the `db-tool` archive-replay verifier (`storage/db-tool/src/replay_on_archive.rs::execute_and_verify`) to bind a locally re-executed `TransactionOutput` to the authenticated, accumulator-proven `TransactionInfo` — checks status, gas, write-set hash, and event-root hash, but does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. The code contains its own acknowledgment of this: [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` is the sole authenticity gate used when transactions are replayed against a set of already-proof-verified `TransactionInfo`s (during `ChunkExecutorInner::verify_execution` and the `replay-verify`/`replay_on_archive` tooling). It hashes and compares the write set, events, gas, and status: [2](#0-1) 

It never touches `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`. Those three fields are exactly the fields that carry the authenticated Merkle roots of the full account/resource state, the hot-state SMT, and (when the trading-native feature set is on) the native-position SMT — i.e. the state-commitment roots that "must survive executor-to-storage handoff unchanged," per the state-integrity invariant this fields is designed to protect. These roots are computed separately in `DoStateCheckpoint::run`/`compute_position_checkpoint` and bound into `TransactionInfoV1` at execution time: [3](#0-2) 

Because `ensure_match_transaction_info` skips these fields, a chunk/archive replay can locally recompute a state (or native-position) root that differs from the one embedded in the trusted `TransactionInfo` — due to a bug in JMT construction, a native-position tree divergence, non-determinism, or an unnoticed protocol change — and `verify_execution`/`execute_and_verify` will still report the replay as successful, since they rely purely on this comparator's `Ok(())`: [4](#0-3) [5](#0-4) 

This is the direct analog of the external report's core lesson: a check that is supposed to validate an invariant before accepting/propagating a result silently omits part of the invariant, letting a corrupted/divergent value pass unflagged.

### Impact Explanation
If the state/position checkpoint roots are wrong but this comparator doesn't catch it, tooling and node operators that rely on chunk executor's `verify_execution` (used with `VerifyExecutionMode` during fast-sync bootstrapping / verified state-sync) and on `replay_on_archive`/backup-verify (used to detect ledger divergence and validate archived history) will falsely conclude the historical or synced ledger state is correct even when the actual JMT / native-position root diverges from the authenticated ledger. Since this is the primary automated safety net for detecting exactly this class of divergence (a hard-fork/consensus divergence in state commitment), a real state-root bug could go undetected by the verification pipeline, and corrupted/divergent state could be silently accepted as valid, which is a state-commitment integrity failure with potentially critical downstream impact (wrong committed state being trusted or served by an "verified" node).

### Likelihood Explanation
The gap only matters when the state/hot-state/position checkpoint hash is actually populated (i.e., `TRANSACTION_INFO_V1`, `HOT_STATE_ROOT_IN_TXN_INFO`, and/or `COMPUTE_TRADING_NATIVE_STATE_ROOTS` on-chain features are enabled) and only manifests if there's already an underlying non-determinism or bug in one of those root computations. The comment in the code itself flags this explicitly as a pre-existing, known gap that must be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`," confirming it is a real, currently-live gap in the verification logic rather than a purely theoretical one. It does not, by itself, corrupt state — it is a validation blind spot that would only surface impact in combination with a state-root computation bug, but it removes an intended safety net for the state-commitment invariant.

### Recommendation
Extend `ensure_match_transaction_info` (and correspondingly `verify_execution` / `execute_and_verify` / any other caller) to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally recomputed values whenever these fields are present in the trusted `TransactionInfo`, mirroring the length/hash checks already done for the write set and events, so that replay/verification tooling cannot report success while missing a divergent state-commitment root.

### Proof of Concept
Not independently reproducible from static analysis alone: this is a missing-check finding rather than a state-corrupting input path. The PoC scenario is: (1) enable `TRANSACTION_INFO_V1` + `COMPUTE_TRADING_NATIVE_STATE_ROOTS`; (2) introduce/trigger any divergence in the native-position (or hot-state) root computation between execution and replay (e.g., differing base snapshot ordering as hinted by comments about "pre-committed position tip" vs "merklized snapshot" lag in `storage/storage-interface/src/state_store/state_summary.rs`); (3) run `verify_execution` or `replay_on_archive`'s `execute_and_verify` against the authenticated `TransactionInfo`s — both will report success despite the state/position root mismatch, because `ensure_match_transaction_info` never inspects those fields.

Note: I was unable to fully verify whether any other independent guard (outside this comparator) re-checks these checkpoint hashes elsewhere in the exact same replay/verify call paths; the code comment strongly suggests no such guard currently exists for the position case. Given the reasoning-effort and tool-call budget constraints, I was not able to build and run an actual reproduction to confirm a live root-divergence bug — this finding documents a confirmed missing invariant check rather than a demonstrated live divergence.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L42-75)
```rust
        let last_checkpoint = state_summary.last_checkpoint();

        let state_checkpoint_hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_state_checkpoints,
            last_checkpoint.root_hash(),
            "state",
        )?;
        let hot_state_checkpoint_hashes = execution_output
            .hot_state_root_in_txn_info
            .then(|| {
                Self::get_state_checkpoint_hashes(
                    execution_output,
                    known_hot_state_checkpoints,
                    last_checkpoint.hot_root_hash()?,
                    "hot_state",
                )
            })
            .transpose()?;

        let (position_state_summary, position_state_checkpoint_hashes) =
            if execution_output.compute_trading_native_state_roots {
                let persisted = persisted_position_state_summary
                    .expect("persisted position summary required when feature on");
                let (summary, hashes) = Self::compute_position_checkpoint(
                    execution_output,
                    parent_position_state_summary,
                    persisted,
                    known_position_state_checkpoints,
                )?;
                (Some(summary), Some(hashes))
            } else {
                (None, None)
            };
```

**File:** execution/executor/src/chunk_executor/mod.rs (L684-706)
```rust
        // not `zip_eq`, deliberately
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
