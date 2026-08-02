### Title
`ensure_match_transaction_info` skips validating checkpoint/state-root hashes, letting replay-verify accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verification tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/cli/src/commands.rs`) to confirm that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` fetched from an archive/proof. It checks status, gas used, write-set hash, and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the authenticated `TransactionInfo`. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` computes and compares `write_set_hash` and `event_root_hash` against `txn_info.state_change_hash()` / `txn_info.event_root_hash()`, but the function ends with only a `TODO` comment acknowledging that the state/hot-state and position-state checkpoint hashes are never validated: [2](#0-1) 

These checkpoint hashes are exactly the fields that commit the Merkle/Jellyfish state root at checkpoint boundaries (see how they are constructed in `assemble_transaction_infos` in `execution/executor/src/workflow/do_ledger_update.rs`, which sets `maybe_state_checkpoint_hash`, `maybe_hot_state_checkpoint_hash`, and `maybe_position_state_checkpoint_hash` on each `TransactionInfo`): [3](#0-2) 

Because `ensure_match_transaction_info` is the sole comparator used by replay-verify tooling (`replay_on_archive.rs`) and the CLI replay path (`aptos-move/cli/src/commands.rs`), a divergence between the locally-computed state root and the authenticated, accumulator-proven state root recorded in the archived `TransactionInfo` will not be detected — the function returns `Ok(())` even though the actual committed state (the Jellyfish Merkle root at a checkpoint) is wrong.

### Impact Explanation
This breaks the proof/commit invariant that state-checkpoint hashes bound into `TransactionInfo` (and thus into the transaction accumulator and any `TransactionInfoWithProof`) must reflect the true state resulting from re-execution. Auditing/replay tooling built on this function (used to detect state divergence from a hard fork, storage corruption, or a VM bug against mainnet archives) would silently pass even when the state root has diverged, defeating the entire purpose of replay verification. This matches the "Hard-fork-only divergence during commit, replay, ... or proof verification" and "authenticated API ... output bound to the wrong version/root" impact classes: the tool is an authenticated verification path whose result is trusted to certify that history replays correctly, and it can certify an incorrect state root as correct.

### Likelihood Explanation
The gap is unconditional (not gated behind a flag) — it is a structural omission in the comparator, not a race condition or attacker-triggered exploit. It would trigger any time a state-checkpoint transaction's actual computed root diverges from the archived `TransactionInfo`'s checkpoint hash (e.g. from a JMT/hot-state computation bug, an in-place state read bug in `position_state`, or an actual chain divergence being investigated) — exactly the scenario replay-verify exists to catch. The author's own inline `TODO` comment confirms this is a known, currently-live gap rather than a hypothetical.

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present/applicable) against the locally-computed roots before returning `Ok(())`, matching how `write_set_hash` and `event_root_hash` are already checked, so replay-verify tooling cannot silently accept a corrupted or diverged state root.

### Proof of Concept
Not independently runnable from static analysis alone — the code comment itself is direct evidence: `types/src/transaction/mod.rs` lines 2197-2202 state "this comparator ignores the checkpoint hashes ..., so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution." A concrete PoC would require constructing a `TransactionOutput` whose write-set/events hash match `txn_info` but whose resulting state-checkpoint root (as computed by the JMT layer) differs from `txn_info.state_checkpoint_hash()`, then calling `ensure_match_transaction_info` and observing it return `Ok(())`.

**Note on investigation limits:** I was unable to fully trace how `replay_on_archive.rs` and the CLI replay path (`aptos-move/cli/src/commands.rs`) consume the result of `ensure_match_transaction_info` (e.g., whether they perform any separate/redundant state-root check elsewhere) since the file contents for `replay_on_archive.rs` could not be retrieved within the available tool calls. If a redundant check exists downstream in that file, the practical severity would be reduced; I recommend a Devin session with full file access to confirm whether any additional state-root check exists in `replay_on_archive.rs` before treating this as final.

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
