## Finding

### Title
`ensure_match_transaction_info` skips state-checkpoint hash validation, allowing replay-verify to accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative check used by `db-tool replay-on-archive` (and the Move CLI replay path) to confirm that locally re-executed transaction results match the transaction info that was actually committed to the ledger. The function validates status, gas used, write-set hash, and event-root hash, but a code comment openly documents that it **does not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. As a result, replay-verify can report a transaction as matching even when the locally-computed state (Merkle) root diverges from the one that was actually committed and authenticated by validator signatures.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  checks:
- transaction status vs. expected status,
- gas used,
- write-set hash (`state_change_hash`),
- event root hash,

but explicitly skips checkpoint hashes: [2](#0-1) 

This is the sole verification routine invoked by the replay-verify tool that compares freshly re-executed `TransactionOutput`s against the archived, ledger-committed `TransactionInfo`s pulled from a backup, as seen in `execute_and_verify`: [3](#0-2) 

`TransactionInfo` (the object actually hashed into the transaction accumulator and thus into every `LedgerInfoWithSignatures`) carries the `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` fields as first-class, hashed components: [4](#0-3) . These are the fields that would catch divergence between a validator's/replayer's locally computed Sparse-Merkle/Jellyfish state root and the one that was actually agreed upon and signed by the validator set. Because `ensure_match_transaction_info` never compares them, replay-verify's pass/fail signal is blind to exactly the class of bug it exists to catch: silent state-root corruption or divergence introduced by an executor, storage-commit, or restore-path defect.

### Impact Explanation
This maps directly to the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "wrong … state proof accepted as valid" impact categories. Replay-verify is Aptos's primary tooling for auditing historical mainnet segments against a fresh re-execution to catch state-commitment bugs before they cause a chain split. If an executor, checkpoint-hash-assembly (e.g., `assemble_transaction_infos` in `execution/executor/src/workflow/do_ledger_update.rs`), or state-store commit bug corrupts only the state-checkpoint root (leaving write-set/event hashes and status/gas intact — plausible since checkpoint hashes are computed from the whole SMT/JMT rather than the individual write set), replay-verify will report success even though the true consensus-critical root diverges. This defeats detection of state-commitment corruption that this exact tool is meant to surface, directly matching the "authenticated ... proof context" and "durable ledger data" integrity gate.

### Likelihood Explanation
The gap is unconditional and applies to every replay-verify invocation; it is not gated by a feature flag before the check runs (the flag `COMPUTE_TRADING_NATIVE_STATE_ROOTS` referenced in the comment governs a different, unrelated computation, not this comparator) [5](#0-4) . Any bug that corrupts state-checkpoint hash assembly/commit without altering write-set/event hashes or execution status would go undetected by this specific tool. Likelihood of the underlying root-corrupting bug is separate, but the detection blind spot itself is 100% reproducible today and requires no special integrated attacker.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (where present in the expected `TransactionInfo`) against the checkpoint hashes computed by local re-execution, failing verification on mismatch, before any release that enables `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or otherwise treats replay-verify as authoritative for detecting state-root divergence.

### Proof of Concept
1. Take an archived range of transactions and their committed `TransactionInfo`s.
2. Locally craft (or hypothesize) an execution/commit bug that alters the checkpoint's Merkle root but preserves write-set bytes, event bytes, gas, and status (e.g., a bug in checkpoint-hash assembly such as `assemble_transaction_infos` at [6](#0-5) , or in `put_stats_and_indices`/state-store commit path).
3. Run `db-tool replay-on-archive` over that range; `execute_and_verify` calls `ensure_match_transaction_info`, which only compares status/gas/write-set-hash/event-root-hash.
4. Because checkpoint hashes are never compared, the tool reports success/no divergence despite the state root actually differing from the one committed on-chain — hiding the bug that would otherwise cause a hard fork.

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

**File:** types/src/transaction/mod.rs (L2440-2461)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct TransactionInfoV1 {
    gas_used: u64,
    status: ExecutionStatus,
    transaction_hash: HashValue,
    event_root_hash: HashValue,
    state_change_hash: HashValue,
    state_checkpoint_hash: Option<HashValue>,
    hot_state_checkpoint_hash: Option<HashValue>,
    auxiliary_info_hash: Option<HashValue>,

    /// Repurposed reserved field; `None` matches the prior BCS encoding.
    position_state_checkpoint_hash: Option<HashValue>,
    placeholder1: Option<HashValue>,
    placeholder2: Option<HashValue>,
    placeholder3: Option<HashValue>,
    placeholder4: Option<HashValue>,
    placeholder5: Option<HashValue>,
    placeholder6: Option<HashValue>,
    placeholder7: Option<HashValue>,
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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L95-123)
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
                let txn_info_hash = txn_info.hash();
                (txn_info, txn_info_hash)
```
