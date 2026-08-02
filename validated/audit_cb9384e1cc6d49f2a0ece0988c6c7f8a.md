## Finding: `ensure_match_transaction_info` does not validate checkpoint root hashes during chunk-replay verification - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used to confirm that a re-executed/replayed `TransactionOutput` matches the `TransactionInfo` recorded on chain (analogous in spirit to the report's "confirmation by the wrong party" theme: here, the confirmation of committed state is done by the wrong/incomplete set of checks). It explicitly skips validation of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, as the code itself documents. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` checks status, gas used, write-set hash (`state_change_hash`), and event root hash against the `TransactionInfo`, but never compares the checkpoint hash fields carried in `TransactionInfoV1` (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) against locally computed values. [2](#0-1) 

This function is invoked in the chunk-executor's replay/verify-execution path, `ChunkExecutorInner::verify_execution`, which re-executes transactions locally and calls `ensure_match_transaction_info` per transaction to confirm the re-executed output matches the recorded `TransactionInfo` before accepting the chunk. [3](#0-2) 

Because the position/hot-state checkpoint roots are never independently validated here, a locally-computed `position_state_checkpoint_hash` (driven by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) or `hot_state_checkpoint_hash` (driven by `HOT_STATE_ROOT_IN_TXN_INFO`) that diverges from the value actually accepted into the accumulator via `TransactionInfoV1` would not be caught by this check.

### Impact Explanation
This gap is explicitly flagged by the code's own TODO comment as a real, acknowledged limitation: "this comparator ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution." [4](#0-3) 

However, I could not confirm this is currently mainnet-exploitable as a state-integrity break, because:
1. The gate flags this affects — `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and `HOT_STATE_ROOT_IN_TXN_INFO` — appear to be new/unreleased feature flags (numbered 122/123) that require `TRANSACTION_INFO_V1` to also be enabled. [5](#0-4) 
2. The actual accumulator root hash and consensus-critical `TransactionInfo` hash are still computed independently and correctly in `DoLedgerUpdate::assemble_transaction_infos`, which does include the hot-state/position checkpoint hashes in the `TransactionInfoV1` builder before hashing. [6](#0-5) 
So the actual committed accumulator root is not corrupted by this gap by itself — the weakness is specifically in the **verification/replay tooling's blind spot**, not in the primary commit path's hash computation. This means the vulnerability class matches "authenticated response/replay tooling accepting a wrong proof-relevant value as valid" only for the debug/verify-execution replay tooling (`db-tool replay_on_archive`, chunk-executor's `verify_execution_mode.should_verify()` path), not for the core consensus-committed ledger state itself. I cannot fully confirm whether any live mainnet code path relies on this comparator as its *sole* correctness check for durable commits (as opposed to an auxiliary consistency check on top of independently-verified accumulator proofs), since `TransactionInfoListWithProof::verify` and accumulator inclusion proofs (which do cover the full `TransactionInfo` hash, including checkpoint hash fields) are the actual gatekeepers for what gets committed to storage in normal chunk-execution/state-sync flows in `chunk_result_verifier.rs`. [7](#0-6) 

### Likelihood Explanation
Low-to-moderate as a genuine mainnet security issue: the feature flags gating this path are marked "permanent" but their comment style and TODO note ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS") suggest they are pre-production/in-development features not yet enabled on mainnet. The impact is confined to local replay/debug divergence detection failing silently, not to accepting a wrong root/proof as valid on the primary consensus/commit path, since that path's hash computation still includes all fields.

### Recommendation
Before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (and similarly `HOT_STATE_ROOT_IN_TXN_INFO`) on any network, extend `ensure_match_transaction_info` to independently recompute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the `TransactionInfo`, matching the TODO already present in the code.

### Proof of Concept
Not applicable as a demonstrable exploit against current mainnet state — the affected feature flags are not confirmed enabled, and the primary accumulator-hash computation path (`DoLedgerUpdate::assemble_transaction_infos`) is unaffected. The only concretely demonstrable effect is that `db-tool replay_on_archive` / chunk-executor "verify execution" mode would report success even if a node's locally computed position/hot-state checkpoint root differs from the archived one, since `ensure_match_transaction_info` never compares those fields.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L684-707)
```rust
        // not `zip_eq`, deliberately
        for (version, txn_out, txn_info, write_set, events) in multizip((
            begin_version..end_version,
            &execution_output.to_commit.transaction_outputs,
            transaction_infos.iter(),
            write_sets.iter(),
            event_vecs.iter(),
        )) {
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
        }
        Ok(end_version)
```

**File:** types/src/on_chain_config/aptos_features.rs (L203-209)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
    /// When enabled, execution populates `TransactionInfoV1`'s hot state root hash, so it
    /// is committed to the ledger accumulator. Requires `TRANSACTION_INFO_V1`.
    HOT_STATE_ROOT_IN_TXN_INFO = 123,
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

**File:** execution/executor/src/chunk_executor/chunk_result_verifier.rs (L36-66)
```rust
impl ChunkResultVerifier for StateSyncChunkVerifier {
    fn verify_chunk_result(
        &self,
        parent_accumulator: &InMemoryTransactionAccumulator,
        ledger_update_output: &LedgerUpdateOutput,
    ) -> Result<()> {
        // In consensus-only mode, we cannot verify the proof against the executed output,
        // because the proof returned by the remote peer is an empty one.
        if cfg!(feature = "consensus-only-perf-test") {
            return Ok(());
        }

        THREAD_MANAGER.get_exe_cpu_pool().install(|| {
            let first_version = parent_accumulator.num_leaves();

            // Verify the chunk extends the parent accumulator.
            let parent_root_hash = parent_accumulator.root_hash();
            let num_overlap = self.txn_infos_with_proof.verify_extends_ledger(
                first_version,
                parent_root_hash,
                Some(first_version),
            )?;
            assert_eq!(num_overlap, 0, "overlapped chunks");

            // Verify transaction infos match
            ledger_update_output
                .ensure_transaction_infos_match(&self.txn_infos_with_proof.transaction_infos)?;

            Ok(())
        })
    }
```
