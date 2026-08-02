### Title
`ensure_match_transaction_info` skips state-checkpoint hash validation, allowing execution/replay verification to accept a divergent committed state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated comparator used by chunk-execution verification (`execution/executor/src/chunk_executor/mod.rs::verify_execution`) and by replay/debug tooling (`aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) to confirm that a locally re-executed transaction produced the same result as the `TransactionInfo` recorded in the trusted accumulator/proof. The function checks status, gas used, write-set hash (`state_change_hash`) and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`.

### Finding Description [1](#0-0) 

The comparator computes and checks `write_set_hash` against `txn_info.state_change_hash()` and `event_root_hash` against `txn_info.event_root_hash()`, then returns `Ok(())` without ever touching `txn_info.state_checkpoint_hash()`, the hot-state checkpoint hash, or `position_state_checkpoint_hash()` — all of which are fields carried in `TransactionInfo`/`TransactionInfoV1` [2](#0-1) . The code itself documents the gap:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```

This comparator is used directly in `ChunkExecutor::verify_execution`, which is the path that re-executes a chunk of transactions during state-sync/backup verification and is supposed to catch divergence between locally-computed and remotely-claimed ledger state [3](#0-2) . Because the state-checkpoint (Jellyfish Merkle state root), hot-state, and native "position" state roots are not compared, a chunk whose write set and events hash correctly but whose actual state-tree root diverges (e.g., due to a bug in state-tree construction, hot-state merge logic, or the new native "position" state committer) would pass `verify_execution`/replay-verify silently.

### Impact Explanation
State-checkpoint hashes bind the authenticated Merkle/JMT root of the entire account/resource state (and, per the newer `position_state_checkpoint_hash`, the native position-state Merkle root) to a given version. `state_change_hash` (write-set hash) only proves that the *individual transaction's own write set* is what was recorded — it says nothing about whether applying that write set actually produced the correct global state root that downstream proofs (state proofs, resource inclusion proofs, restore/replay integrity) depend on. Skipping the checkpoint-hash comparison means:
- `execution/executor/src/chunk_executor/mod.rs::verify_execution` (used by chunk-based replay/backup verification) can report success even when the locally recomputed state root diverges from the authenticated ledger, which is exactly the "hard-fork-only divergence during commit/replay/restore" class this gate targets.
- Tooling built on `ensure_match_transaction_info` (`aptos-debugger`, `cli` replay/benchmark tools) that operators rely on to detect state divergence would fail to flag a genuine state-root corruption, since only the write-set hash and event hash are checked, not the resulting state tree root.

This is a real, currently-shipped gap (not a hypothetical from the external report) that undermines a specific proof-binding invariant: authenticated state roots must be verified to match, not merely the write set that produced them.

### Likelihood Explanation
The gap is unconditionally present in the current comparator logic (no feature flag currently guards it, per the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` TODO comment), and it is on the direct call path for chunk-execution verification. The likelihood of an actual divergence occurring depends on there being a separate bug in state-tree/hot-state/position-state computation elsewhere in the code (the checkpoint-hash bug alone doesn't corrupt state, it only fails to *detect* corruption produced by another root cause). I could not, in the time available, definitively confirm whether an independent, equivalent checkpoint-hash check exists elsewhere in the mainline commit path (as opposed to only in this specific replay-verification comparator) that would still catch such divergence before it is durably committed to the primary ledger. This uncertainty should be resolved before treating this as a standalone critical finding — it should be assessed as a verification/detection gap rather than a proven direct state-corruption bug.

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in `txn_info`) against the checkpoint roots computed by the local re-execution, consistent with the existing TODO. Do this before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or relying on this comparator for verification of native/position state features, and audit all call sites (`chunk_executor::verify_execution`, `aptos-debugger`, `cli`) to ensure they surface the added mismatch as a hard failure rather than a soft warning.

### Proof of Concept
Not independently reproducible as a standalone exploit from local code alone — this is a verification-logic gap. It becomes observable if any component produces a `TransactionOutput` whose write set/events hash matches an expected `TransactionInfo` but whose actual resulting state-checkpoint/hot-state/position-state root differs (e.g., a bug in `storage/aptosdb/src/state_store/hot_state.rs` merge logic or `storage/aptosdb/src/native_state_committer.rs` position-state Merkle updates). In that scenario, `execution/executor/src/chunk_executor/mod.rs::verify_execution` at [4](#0-3)  would call `ensure_match_transaction_info`, which returns `Ok(())` despite the state-root divergence, at [5](#0-4) .

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
