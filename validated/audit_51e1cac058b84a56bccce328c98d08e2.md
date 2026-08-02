### Title
`ensure_match_transaction_info` skips state-checkpoint hash verification, letting replay-verify tooling accept a diverged state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole per-transaction correctness check used by replay/verification tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) to confirm that a freshly re-executed `TransactionOutput` matches the authenticated `TransactionInfo` recorded on-chain/in backups. It checks status, gas, write-set hash, and event-root hash, but explicitly does **not** check `state_checkpoint_hash` (the Sparse-Merkle state root), `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, with a TODO comment acknowledging the gap.

### Finding Description [1](#0-0) 

The function verifies:
- execution status vs. `txn_info.status()`
- `gas_used` vs. `txn_info.gas_used()`
- `write_set_hash` vs. `txn_info.state_change_hash()`
- `event_root_hash` vs. `txn_info.event_root_hash()`

but the state-checkpoint hash — the authenticated Sparse Merkle Tree root that summarizes the *entire world state* at a checkpoint boundary — is never compared, as stated directly in the code:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution.
``` [2](#0-1) 

This function is called from `execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs`, which re-executes transactions from backups and treats a successful `ensure_match_transaction_info` call as proof the replayed execution matches the ledger: [3](#0-2) 

Since `write_set_hash` only authenticates the write set *produced by this single transaction*, it cannot detect state divergence that accumulated from an earlier, undetected root-hash mismatch (e.g., stale/incorrect base state feeding a correct-looking incremental write). The `state_checkpoint_hash` field exists precisely to catch that class of divergence, since it is the root over the full accumulated world state, not just this transaction's delta. By omitting it from the comparator, `replay-verify`/`replay-on-archive` — whose entire purpose is to give a high-assurance signal that independent re-execution reproduces the state committed by consensus — can report success even though the locally computed state root differs from the one authenticated by the ledger.

### Impact Explanation
This breaks the core proof-integrity guarantee: "committed state that differs from the correct VM result... accepted as valid" and "hard-fork-only divergence during ... replay ... verification." Replay-verify is the primary mainnet safety net used to catch execution/consensus divergence bugs before they cause chain splits or before validators trust an archive node's data. If the state root check is silently skipped, a real state-divergence bug (e.g., in the VM, in state-value serialization, or in a native extension) could pass replay-verify undetected, delaying or preventing detection of a hard-fork-class incident. Because state-checkpoint hash is the authenticated anchor tying replayed output to the correct ledger version/root, its omission is a direct violation of the required "Authenticated API or state-view output bound to the wrong version, object, or proof context" invariant.

### Likelihood Explanation
The gap is unconditional in current code — it is not gated behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or any feature flag; the check is simply absent for all three checkpoint hash fields (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`). Any divergence bug that happens not to change the immediate write set hash of the diverging transaction, or that stems from previously-accumulated stale state, will silently pass `replay_on_archive`, `aptos-debugger`, and the `aptos-move/cli` verification paths that call `ensure_match_transaction_info`. I could not fully verify whether the main `state_checkpoint_hash` (as opposed to the newer `hot_state_checkpoint_hash`/`position_state_checkpoint_hash` fields, which appear tied to an in-development "trading-native" feature per the TODO tag) is also checked by some other independent code path in `replay_on_archive.rs` or `aptos_debugger.rs` beyond what I inspected — a full grep of those files for `state_checkpoint_hash` usage found no additional comparison, but the tool's structure was not exhaustively traced due to iteration limits.

### Recommendation
Add explicit `state_checkpoint_hash` (and, once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) comparisons to `ensure_match_transaction_info`, using the actual computed state root of the replayed output vs. `txn_info.maybe_state_checkpoint_hash()` when the transaction is a checkpoint boundary. This restores the intended invariant that replay-verify tooling only reports success when the full ledger state — not merely this transaction's local write set — matches the authenticated record.

### Proof of Concept
1. Construct (or use a fuzz/property test harness) a scenario where a stale/incorrect state value is read by the VM before this transaction executes (e.g., simulate corrupted prior-state via a state-view bug), such that the transaction's own write set and events are unaffected and hash identically to the expected `TransactionInfo`, but the accumulated state root differs.
2. Run this transaction through `execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs`. [4](#0-3) 
3. Observe that `ensure_match_transaction_info` returns `Ok(())` because it never compares `state_checkpoint_hash`, even though the underlying state root diverges — demonstrating that the replay-verify tool would falsely report success for a state that differs from the correct/committed ledger state.

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
