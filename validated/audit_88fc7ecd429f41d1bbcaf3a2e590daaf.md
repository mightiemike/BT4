## Finding

### Title
Replay-verification (`ensure_match_transaction_info`) never checks the state root, silently accepting a wrong ledger state during backup replay-verify - (`types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single authenticity check used by every "replay and compare against the archived `TransactionInfo`" code path in the repo (chunk-executor's `--verify-execution-mode`, `db-tool`'s `replay_on_archive`, and the `aptos-move/cli` transaction-replay command). It checks status, gas, the write-set hash, and the event root hash, but it never recomputes or compares the state-checkpoint root hash (nor the hot-state or position-state checkpoint hashes) against the value recorded in the trusted `TransactionInfo`.

### Finding Description
`ensure_match_transaction_info` explicitly hashes only the raw `WriteSet` and compares it to `txn_info.state_change_hash()`, and separately compares the event root hash — but the function's own comment admits the state-root fields are skipped: [1](#0-0) 

```
        let write_set_hash = CryptoHash::hash(self.write_set());
        ensure!(write_set_hash == txn_info.state_change_hash(), ...);
        ...
        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
```

Critically, `write_set_hash`/`state_change_hash` is only a hash of the *write operations themselves*; it is not the Jellyfish-Merkle state root produced by applying those write ops to the tree. The actual state root (`state_checkpoint_hash`) — the value that authenticates the entire committed ledger state at a version — is never recomputed or compared by this function.

This function is the sole verification primitive used by every "verify replay against a trusted `TransactionInfo`" caller:
- Chunk executor's dedicated verification pass, which re-executes transactions and only calls this comparator (no `DoStateCheckpoint`/root computation involved): [2](#0-1) 
- The `db-tool` archive replay-verify tool (the standalone tool operators use to independently audit historical execution against backups): [3](#0-2) 
- The `aptos-move/cli` single-transaction replay command: [4](#0-3) 

By contrast, the actual commit path used during normal block execution and state-sync chunk application does recompute and validate the checkpoint root against the `TransactionInfo` via `DoStateCheckpoint`'s `maybe_known_state_checkpoints` mechanism: [5](#0-4) 

So the gap is confined to the dedicated verification tooling/mode, not the commit path that actually writes bytes to `AptosDB`. But that tooling exists specifically to detect execution/state divergence (e.g., after upgrades, chain replays, or auditing archived data) — and it is exactly the path an operator or auditor would rely on to catch a wrong-state-root bug independently of the node that produced the (possibly buggy) state in the first place.

### Impact Explanation
Because `ensure_match_transaction_info` never recomputes the JMT root, any divergence in state-root computation that does not also change the write-set bytes (e.g., a bug in JMT node hashing, sharded-state merklization, or hot-state root computation, or corruption introduced during restore) would pass `--verify-execution-mode` and `db-tool replay-on-archive` cleanly even though the resulting state root is wrong. This defeats the core purpose of replay verification: independently confirming committed ledger state against re-execution. This matches the "hard-fork-only divergence during ... replay ... or proof verification" impact category — a wrong state root can silently pass the exact tool meant to catch it.

### Likelihood Explanation
This is not a hypothetical: it is an explicit, currently-shipping gap in the shared comparator function used by all replay-verification entry points, acknowledged in-repo via a TODO. It requires no attacker privilege to trigger — it only requires that a state-root-affecting bug or corruption exists elsewhere (in JMT computation, hot-state, or trading-native position-state work) for this tool to fail to flag it. The comment's own framing ("before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`") shows the team recognizes the check must be closed before that feature ships, confirming the gap is real and consequential once dependent features are enabled.

### Recommendation
Extend `TransactionOutput::ensure_match_transaction_info` (or its replay callers) to actually recompute the state (and, where applicable, hot-state and position-state) checkpoint hash from post-execution state and compare it against `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`, mirroring what `DoStateCheckpoint`'s known-checkpoint validation already does on the commit path. Until this is done, replay-verify tooling (`--verify-execution-mode`, `db-tool replay_on_archive`, CLI replay) should not be treated as a guarantee that the resulting ledger state root is correct.

### Proof of Concept
1. Introduce (or have present) a bug that produces an incorrect state root while leaving the produced `WriteSet` byte-identical to the correct one at the write-op level (e.g., a bug in the sharded state-merkle-db commit or hot-state root aggregation logic that doesn't touch write-op contents).
2. Run `db-tool replay-on-archive` or `aptos move replay` against the backup/archive for the affected version range.
3. Observe `execute_and_verify` / `ensure_match_transaction_info` return `Ok(())` because status, gas, `write_set_hash`, and event root all match — despite the state root being wrong, since `state_checkpoint_hash` is never checked: [6](#0-5) [7](#0-6)

### Citations

**File:** types/src/transaction/mod.rs (L2159-2204)
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

        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
    }
```

**File:** execution/executor/src/chunk_executor/mod.rs (L373-413)
```rust
        let txn_infos = chunk_verifier.transaction_infos();
        let known_state_checkpoints = Some(
            txn_infos
                .iter()
                .map(|t| t.state_checkpoint_hash())
                .collect_vec(),
        );
        let known_hot_state_checkpoints =
            output.execution_output.hot_state_root_in_txn_info.then(|| {
                txn_infos
                    .iter()
                    .map(|t| t.hot_state_checkpoint_hash())
                    .collect_vec()
            });
        let compute_trading_native_state_roots =
            output.execution_output.compute_trading_native_state_roots;
        let known_position_state_checkpoints = compute_trading_native_state_roots.then(|| {
            txn_infos
                .iter()
                .map(|t| t.position_state_checkpoint_hash())
                .collect_vec()
        });
        let position_persisted = compute_trading_native_state_roots
            .then(|| ProvablePositionStateSummary::new_persisted(self.db.reader.as_ref()))
            .transpose()?;
        let state_checkpoint_output = DoStateCheckpoint::run()
            .execution_output(&output.execution_output)
            .parent_state_summary(&parent_state_summary)
            .persisted_state_summary(&ProvableStateSummary::new_persisted(
                self.db.reader.as_ref(),
            )?)
            .maybe_known_state_checkpoints(known_state_checkpoints)
            .maybe_known_hot_state_checkpoints(known_hot_state_checkpoints)
            // Parent position summary is chained across chunks by the commit
            // queue (seeded from the pre-committed position tip); the persisted
            // base supplies cold-key proofs. The known-hash check validates the
            // computed root against the committed TransactionInfos.
            .maybe_parent_position_state_summary(parent_position_state_summary.as_ref())
            .maybe_persisted_position_state_summary(position_persisted.as_ref())
            .maybe_known_position_state_checkpoints(known_position_state_checkpoints)
            .build()?;
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

**File:** storage/db-tool/src/replay_on_archive.rs (L386-406)
```rust
        assert_eq!(executed_outputs.len(), cur_txns.len());

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

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```
