This confirms the finding. `ensure_match_transaction_info` is called by three consumers — `verify_execution` in `execution/executor/src/chunk_executor/mod.rs`, `replay_on_archive.rs` in `storage/db-tool`, and `aptos-debugger`/CLI replay tooling — all of which rely on it as the sole check that re-executed output matches the authenticated on-chain `TransactionInfo`. None of these call sites independently re-check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, and the function itself explicitly documents that it skips them.

### Title
`TransactionOutput::ensure_match_transaction_info` omits checkpoint-hash validation, allowing replay/verify tooling to accept a diverged state root - (File: types/src/transaction/mod.rs)

### Summary
`ensure_match_transaction_info` is the integrity gate used by chunk-executor verification and replay/verify tooling (`replay_on_archive`, `aptos-debugger`/CLI replay) to confirm that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` committed to the ledger. It checks status, gas used, write-set hash, and event root hash, but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the very fields that authenticate the resulting Merkle/JMT/position-state roots. This is acknowledged directly in the code's own `TODO(trading-native)` comment.

### Finding Description
`TransactionInfo` binds a transaction not just to its write-set/event hashes but also to `state_checkpoint_hash` (root of the account state Sparse/Jellyfish Merkle Tree) and, once enabled, `hot_state_checkpoint_hash` and `position_state_checkpoint_hash` (the native trading position-state root), as seen in `TransactionInfoV1`. [1](#0-0) 

`ensure_match_transaction_info` validates only `status`, `gas_used`, `write_set_hash` (`state_change_hash`), and `event_root_hash`, then returns `Ok(())` without touching any checkpoint hash: [2](#0-1) 

The comment inside the function is explicit about the gap: *"this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."* [3](#0-2) 

This function is the actual invariant check used by the chunk executor's `verify_execution`, which iterates re-executed outputs against archived `TransactionInfo`s and treats a passing `ensure_match_transaction_info` as proof of a correct replay: [4](#0-3) 

It is also used directly by the `db-tool`'s `replay_on_archive` execution/verification path and by `aptos-debugger`/`aptos-move/cli` transaction replay to assert a "correct" match: [5](#0-4) 

Because none of these call sites re-derive and compare the checkpoint hashes independently, a re-execution that produces the correct write-set/events/gas/status but a *different* resulting state root (or position-state root, once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled) will still pass `ensure_match_transaction_info` and be reported as a verified match.

### Impact Explanation
This breaks the state-commitment integrity guarantee that replay/verify tooling is meant to provide: a divergence between the locally recomputed Sparse/Jellyfish Merkle root (or the native position-state root) and the one authenticated on-chain in `TransactionInfo` goes undetected. This tooling is the mechanism operators and auditors use to detect state-divergence bugs (e.g., after a VM/logic change, chain-of-custody issue, or a hard-fork-only divergence in state application) — silently passing verification despite a wrong root undermines exactly the check state-integrity tooling exists for. Given `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/position-state features are being actively rolled out (per the TODO), any bug in state-checkpoint or position-state computation would not be caught by this otherwise-authoritative check.

### Likelihood Explanation
The gap is deterministic and unconditional — every call to `ensure_match_transaction_info` skips checkpoint-hash comparison regardless of feature flags, so any actual state-root divergence (whatever its cause) is masked whenever this function is used as the pass/fail oracle for replay correctness. Likelihood of the underlying divergence occurring depends on other bugs, but the detection failure itself is guaranteed.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`'s locally computed `state_checkpoint_hash` (and, where applicable, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against the corresponding fields in `txn_info`, following the same pattern used for `write_set_hash`/`event_root_hash`, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in production, as already flagged by the in-code TODO.

### Proof of Concept
1. Take any transaction range where state-checkpoint hashes are recorded in `TransactionInfoV1` (e.g., `state_checkpoint_hash`, or once enabled, `position_state_checkpoint_hash`).
2. Run `db-tool replay-verify` (`storage/db-tool/src/replay_on_archive.rs`) or `aptos-debugger`/`aptos move replay` (`aptos-move/cli/src/commands.rs`) against a build whose state-checkpoint computation diverges from the original (e.g., a modified/buggy state-checkpoint or position-state root computation) while write-set, events, gas, and status remain identical.
3. Observe that `execute_and_verify` → `ensure_match_transaction_info` returns `Ok(())` and the tool reports a successful replay/verify, even though the recomputed state root does not match `txn_info.state_checkpoint_hash()` / `txn_info.position_state_checkpoint_hash()`. [6](#0-5)

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

**File:** aptos-move/cli/src/commands.rs (L2650-2655)
```rust

                if !self.skip_comparison {
                    txn_output
                        .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                        .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
                }
```
