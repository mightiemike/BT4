### Title
Replay-verify integrity check silently ignores state-checkpoint hash divergence - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info`, the routine used by replay-verification tooling to confirm that locally re-executed transactions match the authenticated `TransactionInfo` recorded on-chain, checks status, gas, write-set hash, and event-root hash, but does **not** check the transaction's state-checkpoint hash (or hot-state / position-state checkpoint hashes). This is explicitly acknowledged in a TODO comment in the function itself.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  verifies four properties of a re-executed `TransactionOutput` against the persisted `TransactionInfo`: execution status, gas used, write-set hash (`state_change_hash`), and event root hash. It explicitly does not verify the state checkpoint hash, as documented directly in the code: [2](#0-1) .

This function is the core correctness check used by two mainnet-relevant tools that re-execute historical transactions and compare the result against the authenticated, accumulator-proven `TransactionInfo`:
- `execution/executor/src/chunk_executor/mod.rs`'s `verify_execution`, which drives `db-tool replay-verify` and CI/ops divergence detection, calling `txn_out.ensure_match_transaction_info(version, txn_info, Some(write_set), Some(events))` at [3](#0-2) .
- `aptos-debugger`'s `print_mismatches`, used to diagnose replay divergences, at [4](#0-3) .
- `storage/db-tool/src/replay_on_archive.rs`, the operational tool for replaying against archived history, which imports the same check (confirmed via grep, though full call site was not read in this pass).

Because `TransactionInfo.state_checkpoint_hash` (and the related hot-state/position-state checkpoint hashes gated by the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature, referenced at [5](#0-4) ) is never compared in this function, a locally re-executed transaction whose resulting state root differs from the state root recorded in the authenticated, accumulator-proven history will still be reported as "matching" by this check. Only the write-set hash, event hash, gas, and status are validated — the state root itself, which is the primary "commitment" of a state transition, is skipped.

### Impact Explanation
The state-checkpoint hash is the authenticated commitment to the entire post-transaction state (the Jellyfish Merkle root), distinct from the write-set hash (which commits only to the delta values written by *this* transaction, not the full resulting state including all prior state). A state-root divergence between VM execution and the previously committed value is exactly the kind of hard-fork-class bug (e.g., a state-computation bug in the VM, a JMT/storage merge bug, or a non-determinism issue) that replay-verify tooling exists to catch. Because this check omits the checkpoint hash comparison, such divergence would go undetected by `ensure_match_transaction_info`, giving false confidence that historical state computation matches the canonical chain even when the ledger's underlying state view has silently diverged. This matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" impact category, since a fork-class state bug could pass replay-verify undetected.

### Likelihood Explanation
Likelihood of the *underlying* state-divergence bug being introduced by a future change is not something this check controls, so exploitability strictly depends on some other bug (VM, storage, or refactor) causing a state root drift. What is certain and directly provable from the code is that the safety net meant to catch such drift (replay-verify's transaction/output-matching check) has a documented gap for the checkpoint hash. This is a real invariant break in the verification path itself, independent of any particular triggering bug, and is already acknowledged as an open item in the code (the TODO), which supports treating it as a genuine, unresolved gap rather than intentional design.

### Recommendation
Extend `ensure_match_transaction_info` to also compute the local state checkpoint hash (and, when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, hot-state and position-state checkpoint hashes) from the locally re-executed output/state view and assert it matches `txn_info.state_checkpoint_hash()` (and the corresponding fields), failing with a descriptive error on mismatch, consistent with how `ensure_transaction_infos_match` in `execution/executor-types/src/ledger_update_output.rs` already performs full `TransactionInfo` equality (which does include the checkpoint hash) for the state-sync path.

### Proof of Concept
This is a logic-gap finding rather than an exploit requiring external input: any state-root divergence during replay (e.g., a hypothetical bug causing `parent_state_summary`/`DoStateCheckpoint` to compute a state root different from the canonical history) will not be flagged by `verify_execution`'s call to `ensure_match_transaction_info` at [3](#0-2)  because the function body never reads or compares `txn_info.state_checkpoint_hash()`, as confirmed by the full body listing at [6](#0-5) .

**Note on confidence**: I was unable to fully verify (due to tool-call limits) whether `storage/db-tool/src/replay_on_archive.rs` invokes `ensure_match_transaction_info` on a genuinely untrusted/mainnet-facing path versus purely an operator-invoked offline tool, and I could not confirm whether any other code path additionally re-validates the checkpoint hash before/after this check (e.g., at the block-level `DoStateCheckpoint` stage) in a way that would make this gap non-exploitable in practice. If a background Devin session is desired, this could be further verified against `execution/executor/src/workflow/do_state_checkpoint.rs` and the full `replay_on_archive.rs` call flow.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L692-705)
```rust
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
```

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L233-246)
```rust
    fn print_mismatches(
        txn_outputs: &[TransactionOutput],
        expected_txn_infos: &[TransactionInfo],
        first_version: Version,
    ) {
        for idx in 0..txn_outputs.len() {
            let txn_output = &txn_outputs[idx];
            let txn_info = &expected_txn_infos[idx];
            let version = first_version + idx as Version;
            txn_output
                .ensure_match_transaction_info(version, txn_info, None, None)
                .unwrap_or_else(|err| println!("{}", err))
        }
    }
```
