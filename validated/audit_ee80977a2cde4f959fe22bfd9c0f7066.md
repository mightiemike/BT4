## Finding

### Title
Replay-verification skips checkpoint-hash validation, allowing `replay_on_archive` to falsely certify divergent state/hot-state/position-state roots - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the sole state-integrity check used by the `storage/db-tool` `replay_on_archive` tool (and by `aptos-debugger`) to confirm that locally re-executed transactions reproduce the authenticated, on-chain-committed `TransactionInfo`. The function validates status, gas, write-set hash (`state_change_hash`) and event root hash, but — as the code itself documents — **it never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`**. This means the tool whose entire purpose is to detect state divergence during replay can report success even when the locally computed Merkle/SMT roots diverge from the authenticated ledger.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  compares only status, `gas_used`, the hash of the raw write set (`state_change_hash`), and the event root hash against the supplied `TransactionInfo`. Immediately before returning `Ok(())`, the code admits:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution.
``` [2](#0-1) 

`state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` are separate, independently-derived Merkle roots produced by incrementally folding writes into the Sparse-Merkle/Jellyfish state tree across the whole ledger history — they are **not** implied by matching `state_change_hash` (which only hashes this single transaction's write ops). A bug in the incremental tree-update logic (e.g., in the "trading-native" position-state tree under `execution/executor/src/workflow/do_state_checkpoint.rs`) can therefore produce a wrong root while `write_set_hash`/`event_root_hash` still match.

The gap is directly exploitable by the verification path in `storage/db-tool/src/replay_on_archive.rs`, whose `execute_and_verify` re-executes archived transactions with `AptosVMBlockExecutor::execute_block` and calls only `ensure_match_transaction_info` — it never runs `DoStateCheckpoint`'s "known-hash" validation that the chunk-executor commit path uses: [3](#0-2) 

By contrast, the normal chunk-executor commit path (`execution/executor/src/chunk_executor/mod.rs`) does pass `known_state_checkpoints`, `known_hot_state_checkpoints`, and `known_position_state_checkpoints` into `DoStateCheckpoint::run()`, which independently validates the computed root against the committed `TransactionInfo`: [4](#0-3) 

That means `replay_on_archive` is the one code path in the tree relying on `ensure_match_transaction_info` as its *only* state-root check, and that check is a known no-op for exactly the checkpoint fields whose purpose is to authenticate the ledger's state root.

### Impact Explanation
`replay_on_archive` exists specifically to detect hard-fork-class divergence: silent bugs where a node's local state root (used to serve authenticated state proofs via the API, and to seed subsequent Merkle proofs/restores) no longer matches the validator-signed ledger. Because the checkpoint-hash comparison is skipped, an operator running `replay_on_archive` against archived data to audit/detect a state-root bug (e.g., in the new trading-native position-state tree) would get a false "all transactions verified" result even though the position/state/hot-state Merkle root has diverged from the authenticated chain state. This directly falls under "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Wrong accumulator root, Merkle proof ... accepted as valid" since the verification tool is the mechanism meant to catch exactly this class of bug and it cannot.

### Likelihood Explanation
This is not a hypothetical rounding edge case — it is an admitted, permanent gap in the code (`TODO(trading-native)`), triggered on every single call to `ensure_match_transaction_info` from `replay_on_archive`/`aptos-debugger`. It requires no privileged access to trigger: any latent bug in state-checkpoint-root computation (particularly in the newer hot-state/position-state ("trading-native") logic) will go undetected by this verification path.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the locally computed equivalents (as already done via the "known checkpoint" mechanism in `DoStateCheckpoint`), or have `replay_on_archive`/`aptos-debugger` additionally invoke the checkpoint-hash validation path before treating a chunk as verified, especially before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

### Proof of Concept
1. Introduce (or trigger) any divergence in incremental state/hot-state/position-state root computation (e.g., a bug in `do_state_checkpoint.rs`'s position-state extend logic) that does not change the raw write-set bytes or events of a transaction.
2. Run `db-tool replay-on-archive` (`storage/db-tool/src/replay_on_archive.rs::verify`) against archived transactions spanning that version.
3. `execute_and_verify` re-executes the block and calls `ensure_match_transaction_info`, which only compares status/gas/write-set-hash/event-root-hash [5](#0-4)  — the divergent checkpoint hash is never inspected, and the tool reports the chunk as successfully verified despite the state root mismatch.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L380-413)
```rust
        let known_hot_state_checkpoints =
            output.execution_output.hot_state_root_in_txn_info.then(|| {
                txn_infos
                    .iter()
                    .map(|t| t.hot_state_checkpoint_hash())
                    .collect_vec()
            });
        let compute_trading_native_state_roots =
            output.execution_output.compute_trading_native_state_roots;
        let known_position_state_checkpoints = compute_trading_native_state_roots.then(|| {
            txn_infos
                .iter()
                .map(|t| t.position_state_checkpoint_hash())
                .collect_vec()
        });
        let position_persisted = compute_trading_native_state_roots
            .then(|| ProvablePositionStateSummary::new_persisted(self.db.reader.as_ref()))
            .transpose()?;
        let state_checkpoint_output = DoStateCheckpoint::run()
            .execution_output(&output.execution_output)
            .parent_state_summary(&parent_state_summary)
            .persisted_state_summary(&ProvableStateSummary::new_persisted(
                self.db.reader.as_ref(),
            )?)
            .maybe_known_state_checkpoints(known_state_checkpoints)
            .maybe_known_hot_state_checkpoints(known_hot_state_checkpoints)
            // Parent position summary is chained across chunks by the commit
            // queue (seeded from the pre-committed position tip); the persisted
            // base supplies cold-key proofs. The known-hash check validates the
            // computed root against the committed TransactionInfos.
            .maybe_parent_position_state_summary(parent_position_state_summary.as_ref())
            .maybe_persisted_position_state_summary(position_persisted.as_ref())
            .maybe_known_position_state_checkpoints(known_position_state_checkpoints)
            .build()?;
```
