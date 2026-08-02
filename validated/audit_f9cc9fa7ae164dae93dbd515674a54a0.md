## Finding: `TransactionOutput::ensure_match_transaction_info` never validates the state-checkpoint (state-root) hashes, breaking replay-verify's proof-binding guarantee

### Title
`replay-verify` accepts a state root that diverges from local VM execution because `ensure_match_transaction_info` never checks `state_checkpoint_hash` — (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single function used to prove that a locally re-executed transaction output matches the archived, ledger-info-authenticated `TransactionInfo`. It checks status, gas used, write-set hash, and event root hash, but the function returns `Ok(())` without ever comparing `state_checkpoint_hash` (or `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against the value computed from local re-execution.

### Finding Description
`ensure_match_transaction_info` [1](#0-0)  validates only four fields of the pair `(TransactionOutput, TransactionInfo)`: transaction status, gas used, write-set hash (`state_change_hash`), and event root hash. It ends with an explicit acknowledgment that checkpoint hashes are skipped: [2](#0-1) 

This function is the sole correctness oracle used by the `db-tool replay-on-archive` verifier, which re-executes archived transactions with the real VM and is supposed to prove the recomputed result matches the ledger-info-signed `TransactionInfo` pulled from backup: [3](#0-2) 

The `state_checkpoint_hash` field of `TransactionInfo` is the field that binds the entire world-state (Sparse Merkle Tree root) at a version to the ledger, and it is exactly the kind of authenticated commitment this framework's threat model designates as an accumulator/root that "must not diverge from the authenticated proof context." Because `ensure_match_transaction_info` never recomputes and compares this root against `expected_txn_infos[idx].state_checkpoint_hash()`, any divergence between the locally computed world state and the historically committed, ledger-info-signed state root is silently accepted as a passing replay.

### Impact Explanation
`replay-verify` is the canonical tool operators and the Aptos team use to prove that full mainnet history is deterministically reproducible by the VM and that a given archive/DB has not diverged from consensus-committed state. Because the state-checkpoint hash is never checked, this tool cannot detect state-root divergence — e.g., from non-determinism, an execution bug, or storage corruption — that changes the actual world state while leaving events/write-set/gas/status unchanged relative to expectations, or where the write set is verified against the expected write set but the *resulting* Merkle-tree checkpoint differs due to a divergent starting state at that point. This defeats one of the core integrity guarantees requested in scope: "Wrong accumulator root ... accepted as valid" and "Hard-fork-only divergence during ... replay ... verification," since a hidden state-root divergence between two full nodes replaying the same history would go completely undetected by this verification path.

### Likelihood Explanation
The gap is unconditional and always exercised: every call from `replay_on_archive.rs` passes through `ensure_match_transaction_info` and the checkpoint-hash comparison is categorically absent — not just for the not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/position-state feature, but for the baseline `state_checkpoint_hash`/`hot_state_checkpoint_hash` fields too. No special trigger or malicious input is needed; the missing check exists by construction on the main, always-executed replay-verify code path.

### Recommendation
Extend `ensure_match_transaction_info` to recompute the local state-checkpoint root (and hot-state/position-state checkpoints where applicable) at checkpoint boundaries and `ensure!` it equals `txn_info.state_checkpoint_hash()` (and the other checkpoint-hash accessors), mirroring the existing pattern used for `write_set_hash` and `event_root_hash`, before this function can be relied upon as a full state-integrity proof for replay-verify.

### Proof of Concept
No PoC transaction sequence is needed since this is a direct code-path/logical proof: read the body of `ensure_match_transaction_info` [1](#0-0)  — there is no branch or `ensure!` referencing `state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`, yet `replay_on_archive.rs::execute_and_verify` [4](#0-3)  treats a successful `Ok(())` return from this call as full proof that the replayed chunk matches the archived, ledger-info-authenticated transaction infos.

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
