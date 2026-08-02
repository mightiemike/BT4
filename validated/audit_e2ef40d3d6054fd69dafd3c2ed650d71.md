### Title
Replay-verify accepts diverged position/hot-state roots because `TransactionOutput::ensure_match_transaction_info` never checks checkpoint hashes - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single correctness gate used by db-tool's `replay_on_archive` (and other debugger/CLI replay paths) to confirm that a freshly re-executed transaction matches the transaction info recorded in the authenticated backup/ledger history. This function deliberately omits comparison of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, which is explicitly acknowledged in a `TODO` comment in the code itself.

### Finding Description
`ensure_match_transaction_info` checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but not the checkpoint-related hashes carried in `TransactionInfoV1`: [1](#0-0) 

The comment directly in the code states the consequence: [2](#0-1) 

This function is the sole per-transaction verification used by `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes historical transactions and compares outputs to the expected `TransactionInfo` loaded from backups before deciding whether the transaction "matches": [3](#0-2) 

Because `state_checkpoint_hash` (the JMT/state root at checkpoint), `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` are never compared here, a divergence in any of those fields between the locally re-executed result and the trusted backup data will not be flagged as a mismatch by this tool.

### Impact Explanation
This breaks the state-proof-integrity guarantee that `replay_on_archive`/replay-verify tooling is meant to provide: that historical state roots committed in the authenticated ledger genuinely match independent local VM re-execution. If a bug in checkpoint/state-root computation (e.g. in `do_state_checkpoint.rs`'s hot-state or position-state root logic) silently produces an incorrect `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, `replay_on_archive` will still report success, masking a hard-fork-class divergence between the authenticated ledger state and correct VM execution. This directly matches the "Wrong accumulator root / state proof accepted as valid" and "authenticated API output bound to the wrong … proof context" impact classes, since replay-verify is the tool node operators and auditors rely on to catch exactly this kind of corruption.

### Likelihood Explanation
This is not attacker-triggerable on its own — it requires an independent state-checkpoint-root computation bug elsewhere (e.g., in the hot-state or native-position checkpoint hash logic referenced by `do_state_checkpoint.rs`/`do_ledger_update.rs`) to actually produce a divergent root. However, given the comment explicitly documents that "replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution," this is a confirmed, currently-shipping gap in verification coverage rather than a hypothetical: any bug in the checkpoint-hash computation paths (trading-native / hot-state features under active development per the surrounding code) would go undetected by this safety net.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on `TransactionInfoV1`) between the locally computed output and the expected `TransactionInfo`, and fail the comparison on any mismatch, before enabling/relying on `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or hot-state-root features in production replay-verify workflows, per the TODO already present in the source.

### Proof of Concept
Not independently exploitable as a state-committing vulnerability by itself; the code-level proof is the explicit gap in `ensure_match_transaction_info` (lines 2139-2204, esp. 2197-2203) combined with its sole use as the correctness oracle in `replay_on_archive.rs::execute_and_verify` (lines 373-405): construct/backup a `TransactionInfoV1` whose `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` differ from what local re-execution actually produces (e.g., via any checkpoint-hash computation bug) while keeping `write_set`, `events`, `gas_used`, and `status` identical — `execute_and_verify` will report no error, i.e., replay-verify passes despite genuine state-root divergence.

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
