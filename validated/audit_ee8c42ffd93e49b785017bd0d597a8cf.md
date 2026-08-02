### Title
Replay-verification skips checkpoint-hash comparison, allowing a diverged state root to be reported as a verified replay - (File: `types/src/transaction/mod.rs`, used by `storage/db-tool/src/replay_on_archive.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole correctness check used by offline replay-verification tooling to confirm that a locally re-executed transaction matches the historical, ledger-committed `TransactionInfo`. It validates `status`, `gas_used`, the write-set hash (`state_change_hash`), and the event root hash, but it never compares the JMT-derived checkpoint hashes (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) carried in `TransactionInfo`. This is a self-acknowledged gap in the code itself.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  checks only 4 fields between the freshly-executed `TransactionOutput` and the expected, ledger-authenticated `TransactionInfo`:
- execution status
- gas used
- write-set hash vs `state_change_hash`
- event root hash

The function body itself contains an explicit `TODO(trading-native)` comment stating: "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution" [2](#0-1) .

This matters because `state_checkpoint_hash` is not derivable from a single transaction's write set alone — it is the root of the *global* Jellyfish Merkle Tree after applying all cumulative state up to that transaction, computed separately in the state-checkpoint/state-summary path (e.g. `execution/executor/src/workflow/do_state_checkpoint.rs`, which does perform this comparison in the normal execution/chunk-update pipeline via `get_state_checkpoint_hashes` [3](#0-2) ). A write-set hash match only proves this one transaction's own state deltas are byte-identical; it does not prove that applying those deltas onto the (potentially already-corrupted or differently-encoded) accumulated state tree produces the correct root.

The offline tool `storage/db-tool/src/replay_on_archive.rs` calls `AptosVMBlockExecutor::execute_block` directly and then calls only `ensure_match_transaction_info` on each output [4](#0-3) . It never invokes the state-checkpoint/state-summary computation (`DoStateCheckpoint`) that would recompute and cross-check the JMT root against the historical `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` recorded in the archived `TransactionInfo`. The same limited comparator is also used from `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs` (both call sites confirmed present, though their surrounding logic could not be fully inspected due to remaining tool-call budget).

### Impact Explanation
This breaks the "authenticated API/proof-bearing response bound to the right root" invariant for the replay-verification path: `replay_on_archive` and related debugger/CLI replay tooling are the primary mainnet-facing mechanisms used to detect state divergence (e.g., hard-fork bugs, JMT/storage-schema bugs, VM nondeterminism) by replaying historical, ledger-authenticated transactions against a locally executed VM and asserting equivalence. If a bug elsewhere in the storage/JMT/state-summary layer corrupts the cumulative state root while leaving each individual transaction's own write set, events, gas, and status unchanged (a very plausible failure mode for hashing/versioning/restore bugs), this verifier will report "replay successful" even though the durable state root has silently diverged from the correct value. This directly undermines the tool whose entire purpose is to catch state-commitment regressions before/after they reach mainnet, i.e. a false negative in the mechanism responsible for detecting exactly the class of high/critical bugs (wrong accumulator/state root) that this Gate targets.

### Likelihood Explanation
The gap is deterministic and unconditional — it does not depend on adversarial input, timing, or race conditions; it always occurs whenever `replay_on_archive.rs` (or the CLI/debugger replay flows using `ensure_match_transaction_info`) is used to verify historical state, because the comparator structurally never receives or checks checkpoint-hash fields. The comment in the code confirms the aptos-core team is aware of this and has explicitly deferred fixing it ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), which corroborates this is a real, currently-unaddressed gap rather than a hypothetical one.

### Recommendation
Extend `ensure_match_transaction_info` (or the call sites in `replay_on_archive.rs`, `aptos_debugger.rs`, and `cli/src/commands.rs`) to recompute the post-transaction state checkpoint (via the same `DoStateCheckpoint`/state-summary path used by the executor) and assert it equals `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` (for the relevant `TransactionInfo` variant/feature configuration) before reporting a chunk/transaction as successfully replayed.

### Proof of Concept
1. Take a historical, ledger-authenticated `TransactionInfo` for version `V` with a known `state_checkpoint_hash = H`.
2. Introduce (hypothetically, to demonstrate the gap) a storage/JMT construction difference that alters the computed root hash for the *cumulative* state at version `V` (e.g., a key-encoding or node-ordering change) without altering the write set of the specific transaction being replayed at `V`.
3. Run `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify` [5](#0-4)  against this transaction: `executed_outputs[idx].ensure_match_transaction_info(...)` is called and only checks status/gas/write-set-hash/event-root-hash, all of which are unaffected by the cumulative-state divergence.
4. The call returns `Ok(())`, and the tool reports a successful replay for version `V`, despite the fact that the real, durable state root at `V` no longer matches `H`.

Note: this analysis relies on static code reading; I was unable to fully trace the `aptos-debugger` and `cli/commands.rs` call sites (their surrounding verification context) or confirm at runtime whether any external wrapper compensates for the gap in those two paths, due to remaining tool-call budget.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L206-220)
```rust
        if let Some(known) = known_state_checkpoints {
            ensure!(
                known.len() == num_txns,
                "Bad number of known {label} hashes. {} vs {}",
                known.len(),
                num_txns,
            );
            if let Some(idx) = last_checkpoint_index {
                ensure!(
                    known[idx] == Some(computed_last_checkpoint_hash),
                    "{label} root hash mismatch with known hashes passed in. {:?} vs {:?}",
                    known[idx],
                    Some(computed_last_checkpoint_hash),
                );
            }
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
