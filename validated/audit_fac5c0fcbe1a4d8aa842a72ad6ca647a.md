### Title
`ensure_match_transaction_info` never verifies state/hot-state/position-state checkpoint hashes, letting replay-verify accept a diverged state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single authenticity check used by replay/verify tooling to confirm that a freshly re-executed transaction matches the archived, backup-signed `TransactionInfo`. It checks status, gas used, write-set hash, and event root hash, but — as an explicit `TODO` in the code acknowledges — it never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. Since these fields carry the JMT/state-root commitments that are actually signed into the ledger's `TransactionInfo`/accumulator, replay tooling can report "success" even when locally recomputed state roots diverge from the authenticated chain state.

### Finding Description
`ensure_match_transaction_info` compares the freshly executed `TransactionOutput` against an `expected TransactionInfo` pulled from backup/archive data: [1](#0-0) 

It validates `status`, `gas_used`, and `write_set_hash` against `state_change_hash`, and the recomputed `event_root_hash` against `txn_info.event_root_hash()`. It then returns `Ok(())` without ever inspecting `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that exist on `TransactionInfo` and are populated during normal commit via `assemble_transaction_infos`: [2](#0-1) 

The code's own comment documents this gap directly above the `Ok(())`: [3](#0-2) 

This function is the sole per-transaction correctness gate used by the `db-tool replay-on-archive` verifier, which drives an executed block through `AptosVMBlockExecutor` and calls `ensure_match_transaction_info` per transaction to decide pass/fail: [4](#0-3) 

The same function is also invoked from `aptos-debugger` and the Move CLI's replay commands, so the gap propagates to every tool built on this shared verification primitive.

Because `state_checkpoint_hash` is the Sparse/Jellyfish Merkle root committed at state-checkpoint boundaries (and `hot_state_checkpoint_hash`/`position_state_checkpoint_hash` are the analogous roots for the hot-state and position-state trees), a divergence here means the locally re-executed state differs from the authenticated ledger state root — yet the check never surfaces it. This is a direct proof/commitment-integrity gap of the type targeted by the scan: state-commitment output that differs from the correct VM result is not caught during replay/verification of a proof-bearing structure.

### Impact Explanation
`TransactionInfo` (and by extension its checkpoint hash fields) is the leaf that the transaction accumulator commits to, and that accumulator root is signed by validators into `LedgerInfo`. If a bug elsewhere in the executor, storage-commit path, or replay/restore logic corrupts the state root construction (e.g., an incorrect JMT update, a stale hot-state snapshot, or a bad position-state checkpoint), `ensure_match_transaction_info` will still return `Ok(())` as long as write-set bytes, gas, status, and events match. This means the primary tool designed to catch state-root divergence during replay-verify — used for auditing archived history and validating executor changes — silently passes over exactly the class of bug it exists to catch. This is a high-impact proof/commitment-integrity gap: it can mask a hard-fork-inducing state divergence, letting a corrupted ledger state pass as "verified".

### Likelihood Explanation
This is not a hypothetical: the gap is explicitly acknowledged in the source comment ("this comparator ignores the checkpoint hashes ... so replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution"), confirming the maintainers are aware the check is incomplete. Any state-root-affecting bug introduced elsewhere in the codebase (including in newer trading-native/position-state code paths under active development, given `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) would not be caught by this otherwise-authoritative verification function until it is fixed.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally computed values whenever they are present/applicable (respecting feature-gating such as `COMPUTE_TRADING_NATIVE_STATE_ROOTS`), and fail loudly (not silently pass) when expected checkpoint hashes exist but cannot be validated locally.

### Proof of Concept
1. Run `storage/db-tool replay-on-archive` (or the CLI/`aptos-debugger` equivalent) against a backup range that includes a state-checkpoint transaction.
2. Introduce (or have present due to another bug) a state root divergence that does not change the write-set bytes/hash, gas used, status, or emitted events for that transaction — e.g., corrupted hot-state or position-state checkpoint bookkeeping.
3. Observe `execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs` still calls `ensure_match_transaction_info`, which only checks status/gas/write_set_hash/event_root_hash, and returns `Ok(())` because none of the checked fields differ.
4. The verifier reports the chunk as successfully replayed despite the state checkpoint root having diverged from the authenticated archive, demonstrating the tool's failure to detect a state-commitment integrity break. [5](#0-4) [6](#0-5)

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

**File:** storage/db-tool/src/replay_on_archive.rs (L349-415)
```rust
    fn execute_and_verify(
        &self,
        executor: &AptosVMBlockExecutor,
        current_version: &mut Version,
        cur_txns: &mut Vec<Transaction>,
        cur_persisted_aux_info: &mut Vec<PersistedAuxiliaryInfo>,
        expected_txn_infos: &mut Vec<TransactionInfo>,
        expected_events: &mut Vec<Vec<ContractEvent>>,
        expected_writesets: &mut Vec<WriteSet>,
    ) -> Result<Option<Error>> {
        if cur_txns.is_empty() {
            return Ok(None);
        }
        let txns = cur_txns
            .iter()
            .map(|txn| SignatureVerifiedTransaction::from(txn.clone()))
            .collect::<Vec<_>>();
        let txns_provider = DefaultTxnProvider::new(
            txns,
            cur_persisted_aux_info
                .iter()
                .map(|info| AuxiliaryInfo::new(*info, None))
                .collect(),
        );
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

        cur_txns.clear();
        cur_persisted_aux_info.clear();
        expected_txn_infos.clear();
        expected_events.clear();
        expected_writesets.clear();

        Ok(None)
    }
```
