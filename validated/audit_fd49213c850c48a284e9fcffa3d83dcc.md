### Title
`TransactionOutput::ensure_match_transaction_info` skips checkpoint-hash validation, letting replay-verify accept a wrong state/hot-state/position-state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by the `replay-on-archive`/replay-verify tooling to confirm that locally re-executing historical transactions reproduces the authenticated on-chain result. It checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but the code explicitly documents (and the implementation confirms) that it does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that actually commit to the Jellyfish-Merkle state root, hot-state root, and native-position-state root. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` is the sole per-transaction correctness check used by `storage/db-tool/src/replay_on_archive.rs`, the tool backing the `replay-verify` CI/production process that is meant to catch state-divergence bugs before they reach mainnet (i.e., exactly the class of bug this task asks to look for: committed state that differs from the correct result, or hard-fork-only divergence during replay/commit). [2](#0-1) 

The comparator computes and checks only:
- `status`
- `gas_used`
- `write_set_hash == txn_info.state_change_hash()`
- `event_root_hash == txn_info.event_root_hash()`

and then contains an explicit developer TODO acknowledging the gap:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [3](#0-2) 

`TransactionInfoV1` carries three independent commitments beyond the write-set/event hashes: `state_checkpoint_hash` (JMT/global state root), `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (native-position state root, gated by `compute_trading_native_state_roots`) — each computed by a separate code path (`do_state_checkpoint.rs`, hot-state root logic, and position-state merklization). [4](#0-3) [5](#0-4) 

Because a matching write-set hash only proves that the *individual* write operations produced by the VM for that transaction are byte-identical to the archived write set, it says nothing about whether the *tree-construction/merklization* logic that folds those writes into the state root(s) is correct. A bug isolated to JMT update logic, hot-state root computation, or the newer native-position-state tree (`PositionStateWithSummary`/`LedgerWithSummary` in `do_state_checkpoint.rs`) could produce a state/hot-state/position root that diverges from the authenticated chain state while every single `ensure_match_transaction_info` check still passes, because none of those three checkpoint hash fields are compared.

### Impact Explanation
Replay-verify is Aptos's designated safety net for detecting state-commitment divergence (including hard-fork-causing bugs) prior to deploying new execution/storage logic to mainnet nodes. If a bug exists in state-root computation (JMT, hot-state, or the new position-state/"trading-native" tree) that does not also corrupt the write set itself, `replay_on_archive`'s `execute_and_verify` loop will report success (`Ok(None)`) for every affected transaction, because it delegates entirely to `ensure_match_transaction_info`, which structurally cannot detect a state_checkpoint_hash/hot_state_checkpoint_hash/position_state_checkpoint_hash mismatch. This is a proof-integrity gap that lets an authenticated but wrong root value pass verification, directly matching the "wrong accumulator/proof/state-commitment field accepted as valid" and "hard-fork-only divergence during replay/commit" impact classes in scope. Its severity is amplified by the fact that this exact code path is intended to gate the safe activation of `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, as documented by the author's own TODO.

### Likelihood Explanation
This is not a hypothetical extrapolation — it is a self-admitted, TODO-flagged gap in code that is currently shipped and actively used by the replay-verify pipeline (`testsuite/replay-verify/main.py` invokes `db-tool`'s `replay_on_archive`). Any state-root computation bug introduced in the JMT, hot-state, or position-state pipelines (all under active development per the `compute_trading_native_state_roots`/`hot_state_root_in_txn_info` feature flags) would silently bypass the one detection mechanism designed to catch it, with no additional compensating check found elsewhere in `replay_on_archive.rs`.

### Recommendation
Extend `ensure_match_transaction_info` (or add a dedicated check called from `replay_on_archive::execute_and_verify`) to compare the computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in `TransactionInfoV1`) against the corresponding roots computed by the local checkpoint/merklization pipeline for that version, failing verification on any mismatch — exactly as the existing TODO comment specifies, and treat this as a blocking prerequisite before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in production.

### Proof of Concept
Not independently reproducible as an end-to-end exploit from the index alone (would require injecting a divergent state-root computation and running `replay_on_archive` to observe the false "success"), but the code-level proof is direct:
1. `replay_on_archive.rs::execute_and_verify` calls `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_infos[idx], ...)` as its only correctness gate. [6](#0-5) 
2. `ensure_match_transaction_info`'s implementation never reads `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` — confirmed by reading the full function body and its own TODO comment. [1](#0-0) 

Given the strict scope of this task (favoring the strongest independently-provable candidate), I report this as the strongest local analog to the external "single bad component silently breaks integrity of the whole system" bug class: a narrow but real, self-documented state-commitment validation gap in the replay/verification path.

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

**File:** execution/executor-types/src/execution_output.rs (L193-203)
```rust
    /// Whether to assemble `TransactionInfoV1` (instead of `TransactionInfoV0`) in the
    /// subsequent state-checkpoint / ledger-update phases.
    pub transaction_info_v1: bool,
    /// Whether to compute the hot state root hash and commit it to
    /// `TransactionInfoV1.hot_state_checkpoint_hash`. Implies `transaction_info_v1`.
    pub hot_state_root_in_txn_info: bool,
    /// Whether to compute the native-position state root at the checkpoint
    /// stage and commit it to `TransactionInfoV1.position_state_checkpoint_hash`.
    /// Implies `transaction_info_v1`.
    pub compute_trading_native_state_roots: bool,
}
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L179-190)
```rust
        // Per-tx hash vector + known-hash validation (shared with main/hot state).
        let hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_position_state_checkpoints,
            new_last_checkpoint.root_hash(),
            "position_state",
        )?;

        let summary =
            LedgerWithSummary::from_latest_and_last_checkpoint(new_latest, new_last_checkpoint);
        Ok((summary, hashes))
    }
```
