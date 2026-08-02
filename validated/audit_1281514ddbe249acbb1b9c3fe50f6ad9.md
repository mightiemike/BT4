Based on my investigation, I found a genuine local integrity gap that's structurally analogous to the reported bug: **code that validates correctness based on an incomplete field checklist, silently omitting a value that governs whether state diverges — exactly like `triggerDefault` trusting `collateral() == 0` while ignoring unaccounted balance.**

### Title
Replay-verification skips state-checkpoint hash comparison, allowing a divergent Merkle state root to pass as a verified match - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole correctness check used by Aptos's replay-verify tooling to confirm that locally re-executed transaction results match the authenticated, backed-up `TransactionInfo` for a version. It compares status, gas used, write-set hash, and event-root hash, but never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. A divergence in the locally computed state root at a checkpoint transaction is therefore never detected by this function, even though it is the field that actually commits to the entire world state.

### Finding Description [1](#0-0) 

`ensure_match_transaction_info` asserts equality of `status`, `gas_used`, the write-set hash against `txn_info.state_change_hash()`, and the event-root hash against `txn_info.event_root_hash()`. It never reads `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`, and the function's own trailing comment documents this gap explicitly:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```

This function is called directly by the primary replay-verify pipeline: [2](#0-1) 

`execute_and_verify` re-executes historical transactions via `AptosVMBlockExecutor` against the state view of the prior version, then calls `ensure_match_transaction_info` against the `expected_txn_info` pulled from backup, using it as the pass/fail gate for the whole chunk. Because `state_checkpoint_hash` (the Sparse Merkle Tree root summarizing the entire account/resource state at a checkpoint) and `hot_state_checkpoint_hash` are excluded from the comparison, a transaction whose write set, events, gas, and status all match, but whose resulting state-tree root diverges from history (e.g. because of a subtle non-determinism bug, a state-checkpoint construction bug, or storage-schema misinterpretation elsewhere in the executor), will be reported as a verified success.

This is structurally the same defect pattern as the reported issue: a control/verification decision is made using an incomplete accounting of the relevant quantity (`collateral()` ignoring unaccounted token balance; here, transaction-info equality ignoring the checkpoint-hash fields), so the check silently passes even though the real invariant (all collateral accounted for / state root matches) is violated.

### Impact Explanation
Replay-verify is Aptos's designated mechanism for catching state-computation divergence — including hard-fork-causing bugs — before new node software is rolled out to mainnet, by replaying historical mainnet transactions and asserting bit-for-bit agreement with the already-committed, signature-authenticated ledger. Because the state/hot-state/position checkpoint hashes are excluded from the match check, this safety net has a blind spot precisely at the transactions (checkpoint transactions, typically the last transaction of a block) that seal in the actual account/resource state for that period. A latent state-computation bug (in `do_state_checkpoint.rs`, the JMT/hot-state update path, aggregator materialization, etc.) that corrupts the committed state root but does not corrupt the write-set bytes, events, gas, or status would ship undetected through replay-verify, and could reach mainnet as a hard-fork-class divergence between nodes or between historical and re-executed state.

### Likelihood Explanation
The gap is unconditional and always present in `ensure_match_transaction_info` — it requires no attacker-controlled input, malicious peer, or privilege; it is simply a missing comparison that runs on every replay-verify invocation of every checkpoint transaction. Triggering the impact requires an underlying state-root-affecting bug elsewhere in the executor, but the checked function's entire purpose is to be the detector for exactly such bugs, so its silent gap directly undermines the safety guarantee that replay-verify is supposed to provide before software goes to mainnet.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the values computed locally (when the transaction is a checkpoint), returning an error on mismatch just as it does for `state_change_hash` and `event_root_hash`. This should be done independent of, and prior to, enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, since the existing state/hot-state checkpoint hashes are already unguarded by this function today.

### Proof of Concept
1. Introduce (or imagine) a state-checkpoint computation bug that changes the resulting Sparse Merkle Tree root for a given block without changing any individual write op's bytes, event contents, gas usage, or status (e.g., an ordering or hashing bug in `do_state_checkpoint.rs`'s hot-state/position-state merge logic).
2. Run `storage/db-tool/src/replay_on_archive.rs`'s `verify()` over the affected version range against a backup containing the correct historical `TransactionInfo`.
3. Observe that `execute_and_verify` calls `ensure_match_transaction_info` [3](#0-2)  and that, because `state_checkpoint_hash`/`hot_state_checkpoint_hash` are never compared inside that function [4](#0-3) , the chunk is reported as verified even though the locally computed checkpoint root differs from the one already committed and signed on-chain.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L373-406)
```rust
        let executed_outputs = executor
            .execute_block(
                &txns_provider,
                &self
                    .arc_db
                    .state_view_at_version(current_version.checked_sub(1))?,
                BlockExecutorConfigFromOnchain::new_no_block_limit(), // TODO(HotState): will need to incorporate some features.
                TransactionSliceMetadata::Chunk {
                    begin: *current_version,
                    end: *current_version + cur_txns.len() as u64,
                },
            )
            .map(BlockOutput::into_transaction_outputs_forced)?;
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
