### Title
`TransactionOutput::ensure_match_transaction_info()` never validates the position/hot-state checkpoint hash, so replay-verify and archive-replay tooling can silently accept a corrupted native-position (trading) state root — (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the single correctness gate used by replay/verify tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/cli/src/commands.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `execution/executor/src/chunk_executor/mod.rs`) to confirm that locally re-executed transaction output matches the `TransactionInfo` recorded on an authenticated ledger/backup. The function checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly skips the `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` fields, with a TODO acknowledging the gap.

### Finding Description
`TransactionInfoV1` carries a `position_state_checkpoint_hash` field [1](#0-0) , populated from `StateCheckpointOutput::position_state_checkpoint_hashes`, which is the per-transaction checkpoint hash of the "native position"/trading-native state tree, computed at execution time and persisted at commit without recomputation [2](#0-1) , and threaded through `DoLedgerUpdate::run` into the assembled `TransactionInfo` before it's appended into the transaction accumulator [3](#0-2) .

The verification routine that is supposed to catch divergence between a locally computed `TransactionOutput` and the trusted, proof-bound `TransactionInfo` is `ensure_match_transaction_info`. It checks `status`, `gas_used`, the write-set hash against `state_change_hash`, and the event root hash — but it explicitly does **not** check `state_checkpoint_hash` or `position_state_checkpoint_hash`, and the code comment states this outright: [4](#0-3) 

This function is the actual gate used by:
- `replay_on_archive.rs`'s `execute_and_verify`, which re-executes historical transactions from backup and calls `ensure_match_transaction_info` as the sole pass/fail signal for each replayed transaction [5](#0-4) .
- `execution/executor/src/chunk_executor/mod.rs`, `aptos-move/cli/src/commands.rs`, and `aptos-move/aptos-debugger/src/aptos_debugger.rs` (all call sites of `ensure_match_transaction_info`).

Because the position/trading-native checkpoint hash is never compared, a divergence in the locally-computed native-position (trading) state root relative to the authenticated `TransactionInfo` in the accumulator/backup is invisible to replay-verify: the tool will report a **successful** replay even though the position state tree it computed differs from the one that was actually committed and proven under the ledger's accumulator root.

### Impact Explanation
This breaks the "proof-and-storage" invariant that replay paths must not silently reinterpret or diverge from committed ledger state. A bug in position/trading-native state computation, storage-schema migration, or JMT batching (e.g. `NativeStateCommitter::apply`, `position_summary_at_commit` in `storage/aptosdb/src/db/aptosdb_writer.rs`) that corrupts the committed position root would go completely undetected by the project's own replay-verify safety net, because the one correctness check that exists for `TransactionInfo` matching deliberately omits this field. This is a hard-fork-class detection gap: on-chain nodes could diverge on the position/trading-native state tree without any replay/verify alarm firing, undermining confidence in mainnet ledger integrity for that subsystem and allowing corrupted committed state to persist unnoticed through the standard verification tooling.

### Likelihood Explanation
The gap is not hypothetical: it is called out by name in the code's own TODO ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), indicating the feature is either not yet fully gated on this check or was shipped with a known hole. Since `position_state_checkpoint_hash` is optional/`None` unless the position-state-root feature is enabled, exploitability/impact is contingent on that feature flag's mainnet activation status, which I could not fully confirm from the available index (I found the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` symbol referenced in `types/src/on_chain_config/aptos_features.rs`, `aptos-move/framework/move-stdlib/sources/configs/features.move`, and `storage/aptosdb/src/db/aptosdb_writer.rs`/`aptosdb_reader.rs`, but I was not able to fully trace its rollout/activation state within the available tool budget).

### Recommendation
Extend `ensure_match_transaction_info` to compare `self.write_set`-derived state checkpoint hash(es) — including `position_state_checkpoint_hash` when present — against the corresponding fields on `txn_info`, exactly as it already does for `state_change_hash` and `event_root_hash`, before enabling/relying on `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in any environment where replay-verify is treated as an integrity guarantee.

### Proof of Concept
No live PoC was constructed (index-only investigation, no execution environment available). The structural proof is the code itself:
1. `position_state_checkpoint_hash` is a real, persisted field of `TransactionInfoV1` [6](#0-5) .
2. `ensure_match_transaction_info` is the only consistency check function tying a `TransactionOutput` to a `TransactionInfo`, and it never reads `position_state_checkpoint_hash` or `state_checkpoint_hash` from `txn_info`, with an explicit acknowledging TODO [7](#0-6) .
3. `replay_on_archive.rs` uses exactly this function as its correctness oracle per replayed transaction [8](#0-7) .

Given the reasoning-effort and iteration budget used, I was not able to confirm whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/native-position roots are active on Aptos mainnet today; a Devin session with full repo/terminal access would be needed to trace the feature flag's activation state and build an executable PoC that forces a position-root mismatch through `replay_on_archive` undetected.

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

**File:** execution/executor-types/src/state_checkpoint_output.rs (L78-92)
```rust
#[derive(Debug)]
pub struct Inner {
    pub state_summary: LedgerStateSummary,
    pub state_checkpoint_hashes: Vec<Option<HashValue>>,
    // TODO(HotState): this is currently None in testnet and mainnet, since we don't run hot state
    // root hashes in consensus or state-sync yet.
    pub hot_state_checkpoint_hashes: Option<Vec<Option<HashValue>>>,
    /// Native-position summary after this chunk (latest + last_checkpoint),
    /// computed at execution time, persisted at commit without recompute.
    /// `None` unless the position-state-root feature is on.
    pub position_state_summary: Option<LedgerWithSummary<PositionStateWithSummary>>,
    /// Per-transaction position state root: `Some` at the checkpoint index,
    /// `None` elsewhere. `None` (the whole option) unless the feature is on.
    pub position_state_checkpoint_hashes: Option<Vec<Option<HashValue>>>,
}
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L30-48)
```rust
        // Assemble `TransactionInfo`s. The variant (V0 vs V1) is driven by the
        // `TRANSACTION_INFO_V1` on-chain feature, threaded via
        // `ExecutionOutput::transaction_info_v1`. The hot state root hash a V1 carries is
        // present only when `HOT_STATE_ROOT_IN_TXN_INFO` is also on (`DoStateCheckpoint`
        // produces `Some` hashes iff so); otherwise the V1 leaves it `None`.
        let (transaction_infos, transaction_info_hashes) = Self::assemble_transaction_infos(
            &execution_output.to_commit,
            execution_output.transaction_info_v1,
            &state_checkpoint_output.state_checkpoint_hashes,
            state_checkpoint_output
                .hot_state_checkpoint_hashes
                .as_deref(),
            state_checkpoint_output
                .position_state_checkpoint_hashes
                .as_deref(),
        );

        // Calculate root hash
        let transaction_accumulator = Arc::new(parent_accumulator.append(&transaction_info_hashes));
```

**File:** storage/db-tool/src/replay_on_archive.rs (L388-406)
```rust
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
