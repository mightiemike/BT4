## Finding

`ensure_match_transaction_info` in `types/src/transaction/mod.rs` is the sole integrity check used by Aptos's replay-verification tooling (`storage/db-tool/src/replay_on_archive.rs` and `aptos-move/cli/src/commands.rs` replay command) to confirm that locally-replayed VM execution matches the authoritative, backed-up `TransactionInfo`. As its own TODO comment states, this comparator checks status, gas, write-set hash, and event root hash, but **never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`** — the fields that authenticate the actual world-state (SMT/JMT) root produced by execution.

### Title
Replay-verification tooling silently accepts state-root divergence because `ensure_match_transaction_info` never validates checkpoint hashes - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` [1](#0-0)  is used as the correctness gate in the standalone replay/audit path [2](#0-1)  and in the CLI transaction-replay command [3](#0-2) . Unlike the write-set and event checks, the function explicitly skips comparing the world-state checkpoint hash against what was actually computed by re-executing the transaction, as documented in its own inline TODO [4](#0-3) .

### Finding Description
`TransactionInfo` carries `state_checkpoint_hash` (and, on `V1`, `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) which is the authenticated commitment to the world state after a transaction/checkpoint [5](#0-4) . In the normal block/chunk execution paths, this value is independently re-derived and cross-checked via `DoStateCheckpoint`'s `known_state_checkpoints`/`known_position_state_checkpoints` machinery [6](#0-5) , so a divergence there is caught. However, the standalone `replay_on_archive` tool and the CLI's single-transaction replay command do **not** run this checkpoint-tree machinery — they execute the transaction with `AptosVMBlockExecutor`/VM directly and rely exclusively on `ensure_match_transaction_info` to declare a match [7](#0-6) . Since that function never inspects `state_checkpoint_hash`, any divergence between the locally-computed world state and the archived, ledger-committed state root is invisible to these tools — they will report "replay successful" even though the state root diverges.

### Impact Explanation
Replay-verification against archived history is a primary mechanism for detecting VM/execution non-determinism or state-computation bugs (the kind of bug that would cause a hard fork or an undetected chain split) after protocol/VM changes. Because the checkpoint-hash fields are unconditionally skipped — not merely gated behind an unreleased feature flag — a state-root-affecting bug (e.g., an incorrect resource serialization, table/JMT-hashing change, or any VM regression that alters state but preserves the write set's serialized bytes/hash and events) can pass `replay_on_archive` and CLI replay-verification as "matching," giving false confidence that historical execution is reproducible and consistent. This masks state-commitment integrity issues exactly in the tooling meant to catch them, matching the "hard-fork-only divergence during commit/replay/restore/proof verification" impact class.

### Likelihood Explanation
This is not a race condition or attacker-triggered exploit — it triggers deterministically whenever `state_checkpoint_hash` (or any of the V1 checkpoint fields) is the only thing that diverges between the archived record and a re-execution, which is realistic whenever there is a subtle bug in state-tree construction/hashing logic that doesn't change the flattened write-set bytes/hash. The gap is unconditional in the current code (the comment ties it to a not-yet-enabled feature, but the check is skipped regardless of any flag), so the tool is silently under-verifying today.

### Recommendation
Extend `ensure_match_transaction_info` to compare the write-set-derived/execution-derived state checkpoint hash(es) against `txn_info.state_checkpoint_hash()` (and hot/position variants when present), or otherwise ensure `replay_on_archive`/`cli replay` compute and cross-check the state tree root the same way `DoStateCheckpoint` does in the standard execution path, before treating a replay as verified.

### Proof of Concept
1. Take an archived (backup) transaction with a known-good `TransactionInfo.state_checkpoint_hash`.
2. Introduce (hypothetically, e.g., via a VM/state-serialization change) an execution divergence that changes the resulting state value/hash for a key without changing the serialized write-set bytes hash consumed by `state_change_hash`, or more directly, patch the state root computation logic.
3. Run `replay_on_archive` or the CLI `replay` command against this transaction.
4. Observe that `ensure_match_transaction_info` returns `Ok(())` because it only checks status, gas, `state_change_hash`, and `event_root_hash` — never `state_checkpoint_hash` — so the tool reports the replay as verified despite the actual state root differing from the authenticated `TransactionInfo`.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2178)
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
```

**File:** types/src/transaction/mod.rs (L2197-2203)
```rust
        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
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

**File:** aptos-move/cli/src/commands.rs (L2805-2813)
```rust
        // When local package overrides are in use the replayed code diverges from
        // what was originally executed on-chain (different instructions, gas, etc.),
        // so output comparison is meaningless and is automatically skipped.
        let skip_comparison = self.skip_comparison || !self.use_local_package.is_empty();
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```

**File:** execution/executor/src/chunk_executor/mod.rs (L373-413)
```rust
        let txn_infos = chunk_verifier.transaction_infos();
        let known_state_checkpoints = Some(
            txn_infos
                .iter()
                .map(|t| t.state_checkpoint_hash())
                .collect_vec(),
        );
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
