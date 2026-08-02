Found a solid analog. The core defect mirrors the external report's pattern exactly: a validity/consistency gate exists and is invoked at the right point, but it silently skips checking a subset of the authoritative fields it's supposed to guard — so replay/restore verification can report success even though the authenticated ledger state has actually diverged.

### Title
`ensure_match_transaction_info` skips state/hot-state/position checkpoint hashes, letting replay-verify and chunk-execution verification accept a state root that diverges from the authenticated ledger — (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative comparator used by chunk-execution verification and replay-verify tooling to confirm that a freshly re-executed `TransactionOutput` matches the `TransactionInfo` that was actually committed to the transaction accumulator (and signed by validators via the `LedgerInfo`). The function checks status, gas, write-set hash, and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that carry the authenticated Sparse-Merkle state root, hot-state root, and native-position state root respectively. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` is called from multiple integrity-verification call sites:
- `execution/executor/src/chunk_executor/mod.rs::verify_execution`, which drives `VerifyExecutionMode` during chunk/backup replay verification and passes results into `ReplayChunkVerifier` [2](#0-1) [3](#0-2) 
- `storage/db-tool/src/replay_on_archive.rs::execute_and_verify`, the archive-replay tool's per-transaction match check [4](#0-3) 
- `aptos-move/cli/src/commands.rs` and `aptos-move/aptos-debugger/src/aptos_debugger.rs`, used to validate individual replayed transactions against on-chain `TransactionInfo` [5](#0-4) [6](#0-5) 

The function's own comment documents the gap:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [7](#0-6) 

These checkpoint hashes are exactly the fields that bind a `TransactionInfo` — and thus the transaction accumulator leaf, and thus the accumulator root inside the signed `LedgerInfo` — to a specific state root: `TransactionInfoV1.state_checkpoint_hash` (world-state SMT root), `hot_state_checkpoint_hash` (hot-state Merkle root, gated by `HOT_STATE_ROOT_IN_TXN_INFO`), and `position_state_checkpoint_hash` (native-position state root, gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) [8](#0-7) . `BlockExecutorConfigFromOnchain::with_features` wires these on-chain feature flags directly into whether the executor computes and commits these roots [9](#0-8) .

Because `ensure_match_transaction_info` never compares these fields, any divergence between the locally-recomputed state/hot-state/position root and the authenticated one recorded in the backup or archive (whether from a storage bug, corrupted backup chunk, or bit-rot) will be reported as a fully matching, verified replay. The write-set hash and event root are checked, but the actual state-root fields that these verification tools exist to protect are left unchecked.

### Impact Explanation
This breaks the proof/commitment invariant that "authenticated API and proof-bearing responses must stay bound to the right ledger version, root, and object," specifically for replay/restore verification tooling. `replay_on_archive` and the chunk-executor's `VerifyExecutionMode` path exist precisely to catch state divergence between recomputed execution and the ledger-authenticated `TransactionInfo`; with this gap, they can pass silently even when the state/hot-state/native-position root is wrong. This directly threatens the reliability of backup verification, archive-node validation, and any downstream trust decisions (e.g., accepting a restored/replayed DB as ledger-correct) that rely on `ensure_match_transaction_info`'s "Ok(())" as proof of correctness. Once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / `HOT_STATE_ROOT_IN_TXN_INFO` are enabled on mainnet, a genuinely corrupted committed state root would not be detected by these tools.

### Likelihood Explanation
The gap is 100% deterministic and unconditional whenever `ensure_match_transaction_info` is invoked with `TransactionInfoV1` outputs — it is not a race condition or timing issue, it is a permanent, code-guaranteed omission (confirmed by the author's own TODO). It doesn't require an attacker; it only requires that a state-root divergence occurs (from any root cause) at a moment when these verification tools are relied upon to catch it. Given the increasing use of `TransactionInfoV1`/hot-state/native-position roots, the tools' silent blind-spot has high likelihood of masking a real divergence when one occurs.

### Recommendation
In `ensure_match_transaction_info`, add explicit checks for `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` whenever the corresponding value is computable/known for the given `TransactionOutput`/`TransactionInfo` pair (i.e., when the transaction is a checkpoint boundary and the relevant feature is enabled), mirroring the pattern already used for `state_change_hash` and `event_root_hash`. This should be done before broader enablement of `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, as the TODO itself states.

### Proof of Concept
1. Enable `TRANSACTION_INFO_V1` + `HOT_STATE_ROOT_IN_TXN_INFO` (and eventually `COMPUTE_TRADING_NATIVE_STATE_ROOTS`).
2. Take an archived/backed-up chunk whose `TransactionInfo.state_checkpoint_hash` (or hot-state/position root) has been corrupted or diverges from what local re-execution would produce (e.g., simulate storage corruption or an execution-path bug affecting only state-root computation, not the write-set itself).
3. Run `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` or the chunk-executor `verify_execution` path over this data.
4. Observe that `ensure_match_transaction_info` returns `Ok(())` because it only compares status/gas/write-set-hash/event-root-hash — the corrupted state/hot-state/position root goes completely unnoticed, and the tool reports "replay succeeded." [4](#0-3) [10](#0-9)

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

**File:** execution/executor/src/chunk_executor/chunk_result_verifier.rs (L129-144)
```rust
pub struct ReplayChunkVerifier {
    pub transaction_infos: Vec<TransactionInfo>,
}

impl ChunkResultVerifier for ReplayChunkVerifier {
    fn verify_chunk_result(
        &self,
        _parent_accumulator: &InMemoryTransactionAccumulator,
        ledger_update_output: &LedgerUpdateOutput,
    ) -> Result<()> {
        ledger_update_output.ensure_transaction_infos_match(&self.transaction_infos)
    }

    fn transaction_infos(&self) -> &[TransactionInfo] {
        &self.transaction_infos
    }
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

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L233-246)
```rust
    fn print_mismatches(
        txn_outputs: &[TransactionOutput],
        expected_txn_infos: &[TransactionInfo],
        first_version: Version,
    ) {
        for idx in 0..txn_outputs.len() {
            let txn_output = &txn_outputs[idx];
            let txn_info = &expected_txn_infos[idx];
            let version = first_version + idx as Version;
            txn_output
                .ensure_match_transaction_info(version, txn_info, None, None)
                .unwrap_or_else(|err| println!("{}", err))
        }
    }
```

**File:** types/src/block_executor/config.rs (L173-189)
```rust
    pub fn with_features(mut self, features: &Features) -> Self {
        self.hotness_in_epilogue = features.is_hotness_in_epilogue_enabled();
        self.transaction_info_v1 = features.is_transaction_info_v1_enabled();
        // Requires transaction_info_v1: the hot state root rides in
        // TransactionInfoV1's hot_state_checkpoint_hash field, which V0 lacks.
        self.hot_state_root_in_txn_info = features.is_hot_state_root_in_txn_info_enabled()
            && features.is_transaction_info_v1_enabled();
        // Requires transaction_info_v1 (the root rides in TransactionInfoV1) and
        // hotness_in_epilogue (only the V1 write-set format it enables serializes
        // the native-position extensions; V0 drops them, so output-replay would
        // diverge). Degrades to off if either is missing.
        self.compute_trading_native_state_roots = features
            .is_compute_trading_native_state_roots_enabled()
            && features.is_transaction_info_v1_enabled()
            && features.is_hotness_in_epilogue_enabled();
        self
    }
```
