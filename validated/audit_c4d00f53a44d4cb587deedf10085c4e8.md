### Title
Replay-verify tool accepts corrupted committed state because `TransactionOutput::ensure_match_transaction_info` never checks the state-checkpoint (Merkle root) hashes - ([File: types/src/transaction/mod.rs])

### Summary
`storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify` is the tool responsible for authenticating that locally re-executed transactions match the transaction history recorded in an archived/backed-up ledger. It delegates all correctness checks to `TransactionOutput::ensure_match_transaction_info` [1](#0-0) . That function only compares status, gas used, write-set hash (`state_change_hash`), and event root hash against the `TransactionInfo` — it never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, and the function's own comment admits this [2](#0-1) .

### Finding Description
`ensure_match_transaction_info` is the single point where a re-executed `TransactionOutput` is checked against the authenticated `TransactionInfo` (the object committed into the transaction accumulator and covered by validator signatures) [3](#0-2) . It verifies status, gas, the write-set hash (`state_change_hash`), and the event root hash [4](#0-3) , but the state Merkle root fields (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) carried by `TransactionInfo` are never compared, as flagged explicitly in the code: *"this comparator ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution"* [2](#0-1) .

This function is the sole verification gate used by `replay_on_archive::Verifier::execute_and_verify`, which is meant to authenticate that a locally re-computed ledger matches the historical, signed record fetched from a backup [5](#0-4) . Because the checkpoint hashes are skipped, any divergence between the locally re-derived world-state root (main state, hot-state, or position-state Jellyfish Merkle root) and the one committed in the authenticated `TransactionInfo` will not be detected. The tool will report the replayed transaction as verified even though the underlying state tree — and thus the values an authenticated state proof would return for that version — is wrong.

### Impact Explanation
This breaks the "committed state that differs from correct VM result" and "authenticated proof output bound to correct version/root" invariants required by the state-integrity gate. `replay_on_archive` (and any downstream tooling relying on `ensure_match_transaction_info`, e.g. `db-tool`'s replay-verify workflows used to validate archive/backup integrity or investigate consensus/state divergence) can silently pass even when a bug in execution, in write-set-to-state materialization, or in the position/hot-state checkpoint computation (`do_state_checkpoint.rs`) produces a state root that differs from the one actually committed to the accumulator. In a hard-fork or state-corruption scenario this means the verification tool used to certify ledger correctness gives a false positive, masking a wrong Merkle root that downstream consumers (light clients, state proofs, restore/backup verification) would otherwise treat as authoritative.

### Likelihood Explanation
The condition is deterministic and always present — it's not a race or timing issue but a structural gap in the verification logic that is exercised on every call to `ensure_match_transaction_info` from `replay_on_archive`. Any bug elsewhere in the codebase that produces a wrong state/hot-state/position-state root (e.g., in `do_state_checkpoint.rs`'s checkpoint hashing path) would go completely undetected by this tool.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` between the locally computed `TransactionOutput`/execution result and the expected `TransactionInfo`, at least when those fields are `Some` in the expected info (i.e., at checkpoint versions). Gate this behind the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature only if the position/hot roots are not yet computed by all callers, but never skip the plain `state_checkpoint_hash` check.

### Proof of Concept
1. Run `db-tool replay-on-archive` against a target DB range that includes a state-checkpoint transaction.
2. Introduce (or trigger via an existing bug) a divergence solely in the computed state root at a checkpoint version — e.g., a bug in write-set application to the state tree that still yields the same write-set/event/gas/status but a different resulting Merkle root (this is plausible because state root computation is a separate downstream step from write-set hashing).
3. Observe that `execute_and_verify` still returns `Ok(None)` (no failure) for that transaction because `ensure_match_transaction_info` never inspects `state_checkpoint_hash` [6](#0-5) , even though the locally derived state root does not match the one bound in the authenticated `TransactionInfo`/ledger.

### Citations

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
