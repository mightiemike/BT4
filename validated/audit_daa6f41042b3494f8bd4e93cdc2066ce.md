Based on my investigation, I found a genuine, code-documented state-integrity gap rather than a hypothetical analog. I was unable to complete verification of a few downstream call sites (`storage/backup/backup-cli/src/backup_types/transaction/restore.rs`, `types/src/on_chain_config/aptos_features.rs`, full `chunk_executor/mod.rs`) before running out of tool calls, so some scope/severity nuances below are noted as unverified.

### Title
`ensure_match_transaction_info` skips checkpoint-hash verification, allowing replay-verify and archive-sync tooling to accept a divergent authenticated state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by the chunk executor's replay/verify path and by backup-cli restore tooling to confirm that a locally re-executed transaction's output matches the authenticated `TransactionInfo` stored in the ledger (and covered by the transaction accumulator). The function checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that bind the authenticated `TransactionInfo` to the actual post-commit state/Merkle root.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  validates only status, gas, write-set hash, and event root hash. The trailing comment is a self-admitted gap: [2](#0-1) 
This states directly that the comparator "ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is invoked from `verify_execution` in the chunk executor, which re-executes a chunk of transactions against locally re-derived `execution_output.to_commit.transaction_outputs` and compares them one-by-one against the persisted, backup-derived `transaction_infos` (i.e., the authenticated proof-bearing objects tied to the accumulator): [3](#0-2) . Because the comparison omits `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, a mismatch between the locally computed state/hot-state Merkle root (or the newer "position" state root referenced by `TransactionInfoV1`, per [4](#0-3) ) and the value bound into the accumulator-verified `TransactionInfo` will not be caught by `verify_execution`.

### Impact Explanation
This falls squarely in the "Hard-fork-only divergence during commit, replay, restore, or proof verification" category: replay-verify tooling (used to validate backups/archives and detect non-determinism or storage corruption across node versions) can report success ("replay verified OK") even though the locally recomputed state root/checkpoint hash disagrees with the authenticated, accumulator-committed value. This defeats the entire purpose of state-checkpoint-hash inclusion in `TransactionInfo` (silent detection of consensus/state divergence, corrupted snapshots, or backup tampering), since the one code path meant to catch such divergence deliberately skips the check.

### Likelihood Explanation
The gap is not theoretical — it is called out by the developers' own TODO, and the feature (`compute_trading_native_state_roots` / `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is referenced throughout the execution/storage stack (`types/src/block_executor/config.rs`, `execution/executor-types/src/execution_output.rs`, `storage/aptosdb/src/db/aptosdb_reader.rs`, `storage/aptosdb/src/db/aptosdb_writer.rs`, `execution/executor/src/workflow/do_get_execution_output.rs`), indicating this is active, non-dead infrastructure rather than a stale comment. I was not able to confirm from the index whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is currently enabled on mainnet — the comment implies it is gated ("before enabling..."), which would limit real-world exploitability until that feature ships, but the checkpoint-hash fields (`state_checkpoint_hash`, `hot_state_checkpoint_hash`) exist unconditionally on `TransactionInfoV0`/`V1` today and are simply never validated by this comparator regardless of the feature flag.

### Recommendation
Extend `ensure_match_transaction_info` to recompute and compare `state_checkpoint_hash` (and `hot_state_checkpoint_hash`/`position_state_checkpoint_hash` when present) against the values produced by local re-execution, using the same source that `do_state_checkpoint.rs` / `execution_output.rs` use to populate these fields, before treating a chunk's execution as verified.

### Proof of Concept
Not applicable as a runnable exploit — the vulnerability is a verification-logic omission proven directly by the in-repo comment and the function body: `ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) never reads `txn_info.state_checkpoint_hash()` or the hot/position variants, so any divergent locally-computed checkpoint hash silently passes verification in `execution/executor/src/chunk_executor/mod.rs:692-706`.

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

**File:** types/src/transaction/mod.rs (L2261-2284)
```rust
    #[builder(finish_fn = build)]
    pub fn builder_v1(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        hot_state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
        position_state_checkpoint_hash: Option<HashValue>,
    ) -> Self {
        Self::V1(TransactionInfoV1::new(
            transaction_hash,
            state_change_hash,
            event_root_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            gas_used,
            status,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
        ))
    }
```

**File:** execution/executor/src/chunk_executor/mod.rs (L684-706)
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
```
