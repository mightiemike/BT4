## Finding

### Title
`TransactionOutput::ensure_match_transaction_info` never validates the state-checkpoint (SMT root) hash, letting replay/verify tooling accept a wrong post-execution state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative comparator used to confirm that a freshly-executed `TransactionOutput` matches the previously committed/archived `TransactionInfo` for the same version. It checks execution status, gas used, write-set hash (`state_change_hash`), and event root hash, but it never compares the resulting state (Merkle) root — `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — against the archived `TransactionInfo`. [1](#0-0) 

### Finding Description
The function performs three checks and then returns `Ok(())` with an explicit acknowledged gap in an inline TODO: [2](#0-1) 

Despite the comment framing this as only affecting the new "trading-native" fields (`hot_state_checkpoint_hash`, `position_state_checkpoint_hash`), the code never touches `state_checkpoint_hash` either — the field that has existed since `TransactionInfoV0` and represents the root hash of the Sparse Merkle Tree describing world state after the transaction: [3](#0-2) 

This function is the sole verification entry point used by `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify`, which re-executes archived transactions with `AptosVMBlockExecutor` and compares the freshly computed `TransactionOutput` against the `expected_txn_info` pulled from backup/archive storage: [4](#0-3) 

Because `ensure_match_transaction_info` never asserts `state_checkpoint_hash` equality, a divergence between the locally re-executed state root and the archived, previously-authenticated state root (e.g. caused by an executor/VM bug, a JMT computation bug, or a corrupted/tampered backup manifest that still carries a valid write-set/event hash) is silently accepted as "matching" by this comparator. The tool is designed specifically to catch exactly this class of divergence (its name is `replay_on_archive` / "Verifier"), yet the state-root leg of the check is missing.

### Impact Explanation
`state_checkpoint_hash` is the field that ultimately feeds into the ledger's accumulator/transaction-info hash and is what auditors and replay-verify infrastructure rely on to detect state divergence from historical execution (hard forks, consensus bugs, storage corruption). Silently passing verification when the state root differs means: (1) replay-verify tooling used to certify historical correctness of full/archive nodes can report false positives, masking a real divergence in committed state; (2) any downstream process that trusts a "successful replay-verify" as proof that the ledger's state commitments are correct is misled. This falls squarely within the requested scope of "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "authenticated API or state-view output bound to the wrong version, object, or proof context" — the comparator is used precisely to bind the freshly executed state to the correct committed proof context, and fails to do so for the state-root component.

### Likelihood Explanation
No malicious/privileged action is required to trigger the check being ineffective — it's always ineffective, because the code path structurally never compares `state_checkpoint_hash`. The failure to detect divergence is deterministic: any executor bug, or any archive/backup tampering that preserves write-set/event hashes, will pass this check every time it runs. The existing TODO comment in the code itself acknowledges the gap for the newer fields but does not identify that the pre-existing `state_checkpoint_hash` is also excluded, meaning this is not a well-understood/tracked issue for the base field.

### Recommendation
In `TransactionOutput::ensure_match_transaction_info` (`types/src/transaction/mod.rs`), when `txn_info.state_checkpoint_hash()` is `Some(_)` (i.e., a checkpoint boundary), independently compute the resulting SMT root from applying the output write set to the pre-state and assert it equals `txn_info.state_checkpoint_hash()`, mirroring the existing pattern used for `state_change_hash` and `event_root_hash`. Extend this to `hot_state_checkpoint_hash` and `position_state_checkpoint_hash` once/if those become active, as the existing TODO suggests, but do not defer the base `state_checkpoint_hash` check, since it is unconditionally relevant to today's replay/verify correctness guarantees.

### Proof of Concept
1. Take any archived transaction range where a checkpoint transaction sets `state_checkpoint_hash` in the persisted `TransactionInfo`.
2. Re-execute the same transactions locally through `AptosVMBlockExecutor` such that the resulting write set and events are identical (e.g., by manually constructing a `TransactionOutput` with the same `write_set`/`events`/`gas_used`/`status` as the original, which is sufficient to satisfy every existing `ensure!` in `ensure_match_transaction_info`) but which corresponds to a different final state root (this is possible because `state_checkpoint_hash` is not derived purely from the local transaction's write set — it depends on the entire pre-existing tree state, so a tree computed from a corrupted/tampered prior state can still produce the same write-set hash for this transaction while yielding a different root).
3. Call `output.ensure_match_transaction_info(version, &txn_info, ..)` as done in `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` — the call returns `Ok(())` even though `state_checkpoint_hash` differs, because the function never reads or compares that field. [5](#0-4)

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

**File:** types/src/transaction/mod.rs (L2405-2412)
```rust
    /// The hash value summarizing all changes caused to the world state by this transaction.
    /// i.e. hash of the output write set.
    state_change_hash: HashValue,

    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,
```

**File:** storage/db-tool/src/replay_on_archive.rs (L373-405)
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
```
