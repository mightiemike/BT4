## Title
Replay-verify comparator omits state/hot-state/position checkpoint hash validation, allowing undetected state-root divergence during archive replay - (types/src/transaction/mod.rs, storage/db-tool/src/replay_on_archive.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` — the single comparator used by `db-tool`'s `replay_on_archive` (and by `aptos-debugger`) to validate that a freshly re-executed transaction matches the transaction data recorded in a backup archive — checks only `status`, `gas_used`, `write_set_hash`, and `event_root_hash`. It does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, even though these fields exist on `TransactionInfo` and are committed into the transaction accumulator (and hence bound into consensus-signed ledger info) exactly like the other fields.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` at `types/src/transaction/mod.rs:2139-2204` verifies transaction status, gas used, write-set hash, and event root hash against a supplied `TransactionInfo`, but explicitly skips the state-checkpoint-related hashes: [1](#0-0) 

The code contains an acknowledging comment stating that this allows `replay_on_archive` to "report a successful replay even when the authenticated position state root diverges from local execution," and explicitly instructs future maintainers to "validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" — but this has not been done.

This comparator is the sole verification gate in `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes historical transactions from a backup archive with the current VM and calls `ensure_match_transaction_info` on each output to decide whether the replay "passed": [2](#0-1) 

Meanwhile, `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` are all real, protocol-committed fields of `TransactionInfo` — they are computed in `DoStateCheckpoint::run` / `compute_position_checkpoint` (`execution/executor/src/workflow/do_state_checkpoint.rs:36-190`) and folded into the per-transaction `TransactionInfo` hash that is appended to the transaction accumulator in `DoLedgerUpdate::assemble_transaction_infos` (`execution/executor/src/workflow/do_ledger_update.rs:58-126`). During live block/chunk execution, these hashes are cross-checked against `known_state_checkpoints` etc. (`do_state_checkpoint.rs:206-220`) — so a *live* execution or chunked-restore path does still validate divergence in these roots when compared to accumulator-embedded values. But `replay_on_archive` does not go through that machinery at all: it independently re-executes raw transactions and compares only via `ensure_match_transaction_info`, which silently ignores these fields.

### Impact Explanation
`replay_on_archive` / `replay-verify` (see `testsuite/replay-verify/main.py`) is the tool used to catch VM/execution non-determinism and unintended state-root changes across historical mainnet data before shipping a release — i.e., it is one of the primary safety nets against a silent hard fork. Because the comparator omits the state/hot-state/position checkpoint hashes, a code change (in the framework, Move VM, or the new "trading native"/position-state feature) that alters computed state roots without altering the write set, events, gas, or status would pass `replay_on_archive` as a clean bit-for-bit replay, while the recomputed state/hot-state/position root actually diverges from what was authenticated on-chain. This is exactly the "hard-fork-only divergence during commit, replay, restore, or proof verification" class in scope: a bug class that would otherwise be caught pre-release is masked by the tool meant to catch it, increasing risk that a state-root-altering regression reaches mainnet undetected until nodes actually disagree post-upgrade.

### Likelihood Explanation
This requires no attacker action — it is a genuine gap in an internal defense-in-depth tool. Likelihood of it "firing" depends on some other change introducing a state-root-affecting but write-set-preserving divergence (e.g., changes to Merkle-tree structure, position-state/"trading native" feature computation, or hot-state promotion logic) slipping through code review — plausible given the code explicitly says the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature is not yet safe to enable without this fix. The maintainers' own TODO comment corroborates this is a known, unresolved gap rather than a false positive.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on the transaction being checkpointed) against values recomputed from the replayed `TransactionOutput`/state view, mirroring the check already performed in `DoStateCheckpoint::get_state_checkpoint_hashes`. Until this is done, `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and any position-state-dependent feature should not be considered validated by `replay_on_archive`/`replay-verify` runs.

### Proof of Concept
Not directly exploitable by an external actor (no privileged/attacker action is required); it is a tooling-integrity gap. Conceptual reproduction:
1. Take a backup archive containing transactions committed with `TransactionInfoV1` including a non-trivial `position_state_checkpoint_hash`.
2. Modify (locally) the position-state root computation (e.g., in `compute_position_checkpoint`) to produce a different but internally consistent hash, without changing any write set, event, gas, or status.
3. Run `storage/db-tool/src/replay_on_archive.rs`'s verifier against the archive with this modified binary.
4. Observe `execute_and_verify` reports success (`ensure_match_transaction_info` returns `Ok`) despite the state root having silently diverged from the archived/authenticated value — confirming the comparator's blind spot documented at `types/src/transaction/mod.rs:2197-2202`.

### Citations

**File:** types/src/transaction/mod.rs (L2196-2204)
```rust

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
