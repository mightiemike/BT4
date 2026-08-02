Confirmed: `storage/db-tool/replay_on_archive.rs` never references `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` at all — the only integrity check it performs on the produced `TransactionOutput` is via `ensure_match_transaction_info`, which by its own documented TODO skips checkpoint-hash validation entirely.

### Title
Replay-verify integrity check omits state/hot-state/position checkpoint root validation, allowing silent state-root divergence to pass as "verified" - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole per-transaction integrity gate used by `storage/db-tool/src/replay_on_archive.rs` (the tool operators/auditors run to confirm that locally re-executing archived transactions reproduces the authenticated, signed ledger state). This function validates status, gas, write-set hash, and event-root hash against the archived `TransactionInfo`, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that bind the recomputed state Merkle/JMT root to the authenticated ledger. This is analogous to the Opus bug: a check meant to gate an invariant (here, "recomputed state root == authenticated state root") is structurally incapable of catching the violation it is supposed to catch, so the caller proceeds believing the invariant holds.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  checks status, gas used, write-set hash (`state_change_hash`), and event root hash, then returns `Ok(())` immediately afterward: [2](#0-1) 

The comment is the developers' own acknowledgment: *"this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."*

`storage/db-tool/src/replay_on_archive.rs::execute_and_verify` calls `execute_block` (raw VM execution, bypassing the executor's ledger-update/state-checkpoint pipeline) and then calls only `ensure_match_transaction_info` to decide pass/fail: [3](#0-2) . Grepping the entire `storage/db-tool` crate confirms no other code path in this tool references `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the state/JMT root produced by local re-execution is never compared against the archived, ledger-info-signed root at all. Note that `TransactionOutput` returned from `execute_block` does not even carry a checkpoint hash field; that value is only computed downstream in the full executor pipeline (`execution/executor/src/workflow/do_state_checkpoint.rs`), which `replay_on_archive` does not invoke.

This differs from the Opus bug in *shape* (there it was an inverted arithmetic comparison; here it is a comparison that was never implemented for a subset of fields) but is the same *class*: an integrity-gating function silently omits a required invariant check, so the caller treats an unverified condition as verified.

### Impact Explanation
`replay_on_archive` is one of the project's primary tools for independently confirming that the archived/authenticated chain state (state Merkle root, hot-state root, position-state root bound into `TransactionInfo` and ultimately into the signed `LedgerInfo`) matches deterministic local re-execution. Per the gate's own criteria, this covers "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "wrong ... state proof accepted as valid." Because the checkpoint-hash comparison is a no-op, any divergence between the state actually committed on-chain and what local execution recomputes — whether from a storage/state-merge bug, a JMT construction bug, or corrupted archived data — will not be flagged by this tool even though write-set hash and event-root hash match. This can mask real state-commitment corruption (including the exact class of hard-fork-detection failure the task scope calls out) during audits, replay-verification pipelines, and incident investigations, giving false assurance that historical/archived ledger state is correct when the state root actually diverged.

### Likelihood Explanation
This is not a hypothetical: the gap is deterministic and always present whenever `replay_on_archive` is run (the checkpoint-hash fields are unconditionally skipped for every transaction, not just under some feature flag), and the code authors have already documented it as a known, unresolved issue tied to enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`. Any state-root divergence introduced by unrelated bugs (in the JMT layer, hot-state promotion, or the position-state checkpoint mechanism) would go undetected by this specific tool with 100% certainty, though it would not by itself corrupt live consensus-committed state.

### Recommendation
Extend `ensure_match_transaction_info` to accept the expected `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` (when produced) and assert equality, mirroring the pattern used for `write_set_hash` and `event_root_hash`. `replay_on_archive.rs` should be updated to run transactions through (or reproduce the output of) the state-checkpoint stage so these hashes are available for comparison, and to fail replay verification on mismatch rather than silently ignoring it. This should be resolved before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, as the comment itself warns.

### Proof of Concept
Code-level proof (no live exploit needed, since this is a self-documented detection gap):
1. `ensure_match_transaction_info` body ends at `Ok(())` right after the TODO comment, with no code path referencing `state_checkpoint_hash` — [4](#0-3) .
2. `replay_on_archive.rs::execute_and_verify` uses this function as the exclusive per-transaction pass/fail oracle — [5](#0-4) .
3. A repo-wide grep of `storage/db-tool/**` for `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` returns zero matches, confirming no alternate verification path exists in this tool.
4. Consequence: construct (or naturally encounter) a scenario where write-set bytes and events are unchanged/equal but the resulting state-tree root differs from the archived authenticated root (e.g., a bug in hot-state/position-state merge logic) — `replay_on_archive` will report the transaction as verified/matching regardless.

Uncertainty: I could not fully verify whether some other, currently-disabled code path (gated behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, which is not yet enabled per the feature flag file) is intended to add this check before that flag goes live, since the flag's activation logic in `aptosdb_reader.rs`/`aptosdb_writer.rs` was only partially inspected. This does not change the current-state finding: as shipped, the check is absent and self-acknowledged as absent in the code comment.

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
