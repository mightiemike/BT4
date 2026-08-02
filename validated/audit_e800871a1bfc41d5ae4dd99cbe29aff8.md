### Title
Replay-verification (`ensure_match_transaction_info`) omits state/hot-state/position checkpoint hash comparison, allowing state-root divergence to pass verification undetected - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info`, the function used by Aptos's replay-verification tooling to confirm that VM re-execution reproduces the historically committed ledger state, only compares `status`, `gas_used`, `write_set` hash (`state_change_hash`), and `event_root_hash` against the archived `TransactionInfo`. It does **not** compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. As a result, a bug that corrupts only the Sparse-Merkle state root, hot-state root, or position-state root (while leaving the raw write set and events untouched) will be silently accepted by replay-verify as a successful, matching replay.

### Finding Description
`ensure_match_transaction_info` is the authoritative correctness check invoked by `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify` (and by the debugger/CLI paths) to validate that re-executing archived transactions against `AptosVM` produces the same result that was originally committed to the chain: [1](#0-0) 

The function itself explicitly documents the gap: [2](#0-1) 

Only `status`, `gas_used`, the write-set hash (`state_change_hash`), and `event_root_hash` are validated. The `TransactionInfo` type stores three additional commitment fields — `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — none of which are checked here: [3](#0-2) 

These checkpoint hashes are computed independently from the write set — they summarize the entire Sparse-Merkle state tree (and hot-state/position trees) after applying the write set, not merely the write set contents. A bug in state-tree update logic, hot-state promotion, or the position-state tree (all downstream of the write set, but distinct commitments) could cause the locally recomputed checkpoint root to diverge from the one originally committed on-chain, while the write set and events remain byte-identical. Since replay-verify never re-derives or compares these checkpoint hashes, such a divergence produces no error from `ensure_match_transaction_info` and the archive/replay job reports success.

### Impact Explanation
This breaks the proof/commitment integrity guarantee that replay-verify tooling is relied upon to provide: it is Aptos's primary defense for detecting non-determinism or execution bugs between VM versions/hardware by replaying the authenticated historical chain and comparing outputs. If a state-checkpoint-only divergence bug is introduced (e.g., in Jellyfish Merkle update, hot-state summary computation, or the new position-state tree logic gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`), replay-verify across the entire archive will not flag it, delaying detection of a state-corrupting or hard-fork-inducing bug until it manifests as a consensus/state-sync failure on live validators. This matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" impact category.

### Likelihood Explanation
This is not a remotely triggerable attack by itself — it requires a separate defect in state-tree/hot-state/position-tree computation to exist. However, given that `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` are exactly the kind of subtle, feature-flag-gated fields (new `TRANSACTION_INFO_V1`/hot-state/position-state features) most likely to contain bugs during active development, and that the code's own comment acknowledges the gap ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"), the likelihood of this blind spot masking a real divergence during the current active development of these features is non-trivial.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` whenever they are present in both the locally computed output and the historical `TransactionInfo`, so replay-verify tooling cannot report success in the presence of a state-root divergence.

### Proof of Concept
1. Introduce (hypothetically, for testing) a change that alters how the post-write-set Sparse Merkle root or hot-state root is computed, without changing the write set or events (e.g., a bug in `state_checkpoint_output` calculation consumed by `DoLedgerUpdate::assemble_transaction_infos`, whose `state_checkpoint_hash`/`hot_state_checkpoint_hash` inputs come from a separate computation pipeline: [4](#0-3)  ).
2. Run `storage/db-tool/src/replay_on_archive.rs` against an archive node containing the affected transactions.
3. Observe that `execute_and_verify` calls `ensure_match_transaction_info` [5](#0-4)  and, because it never compares the checkpoint-hash fields, returns `Ok(None)` for the divergent transaction — the tool reports the replay chunk as fully verified despite the ledger state root having actually diverged from mainnet's committed value.

### Citations

**File:** storage/db-tool/src/replay_on_archive.rs (L392-405)
```rust
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
```

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L82-110)
```rust
                let state_checkpoint_hash = state_checkpoint_hashes[i];
                let event_hashes = txn_output
                    .events()
                    .iter()
                    .map(CryptoHash::hash)
                    .collect::<Vec<_>>();
                let event_root_hash =
                    InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash();
                let write_set_hash = CryptoHash::hash(txn_output.write_set());
                let status = txn_output
                    .status()
                    .as_kept_status()
                    .expect("Already sorted.");
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
```
