### Title
`ensure_match_transaction_info()` skips checkpoint-hash validation, allowing state-root divergence to pass replay/execution verification - (`File: types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the function used by the chunk executor's self-verification path (`verify_execution` in `execution/executor/src/chunk_executor/mod.rs`) and by the `db-tool replay-on-archive` verifier (`storage/db-tool/src/replay_on_archive.rs`) to confirm that a freshly re-executed `TransactionOutput` matches the trusted, already-committed `TransactionInfo`. The function checks status, gas used, write-set hash, and event-root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that actually commit to the post-execution state (Merkle/JMT) root. This gap is self-documented in the code with a `TODO(trading-native)` comment.

### Finding Description
In `types/src/transaction/mod.rs`, `ensure_match_transaction_info` performs: [1](#0-0) 
It verifies status, gas, and write-set hash, then event root hash: [2](#0-1) 
but ends with an explicit acknowledgement that checkpoint hashes are never compared: [3](#0-2) 

This routine is invoked from two integrity-relevant call sites:
1. `ChunkExecutorInner::verify_execution`, part of the chunk-executor's own "verify VM re-execution against provided proofs" workflow used during backup/fast-sync bootstrap verification: [4](#0-3) 
2. `db-tool`'s `replay_on_archive::Verifier::execute_and_verify`, the tool operators run to confirm an archived ledger's committed state matches independent VM re-execution: [5](#0-4) 

Because `TransactionInfo` (V0/V1) carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as first-class fields that summarize the post-txn Jellyfish-Merkle / hot-state / position-state roots: [6](#0-5) 
these values are the actual state-commitment binding, yet neither `verify_execution` nor `replay_on_archive` cross-checks them against the freshly recomputed roots. A ledger whose committed `TransactionInfo.state_checkpoint_hash` diverges from what local VM re-execution actually produces (e.g., due to a storage corruption, backup tampering, or a state-computation bug) would pass both of these "verification" code paths silently, since only the write-set hash, event hash, gas, and status are checked — none of which reflect a diverging state root at a state-checkpoint boundary.

### Impact Explanation
This is scoped to verification/diagnostic tooling rather than the primary consensus/state-sync commit path (the actual chunk-commit integrity gate is `ChunkResultVerifier`/`ensure_transaction_infos_match`, which hashes the entire `TransactionInfo` including checkpoint fields — I could not fully retrieve its body due to index limits, but based on `StateSyncChunkVerifier::verify_chunk_result`'s use of transaction-info-hash equality this path is not affected: [7](#0-6) ).

The exposure is that `verify_execution` (invoked when `VerifyExecutionMode::should_verify()` is set — used for fast-sync bootstrap and backup-restore verification workflows) and `db-tool replay-on-archive` (the operator-facing tool used to independently confirm archived-DB integrity) can both report a clean/successful verification even when the authenticated state-checkpoint root in the already-committed ledger diverges from the state root that local, correct VM execution actually produces. This means a corrupted or tampered `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` in an archived/synced ledger would not be caught by these tools, undermining their entire purpose (silent replay-verify escape) — a hard-fork/state-divergence-class issue in the proof/replay verification pivot named in scope.

### Likelihood Explanation
The gap is deterministic and always present — it is not an edge case but the normal, unconditional code path for every call to `ensure_match_transaction_info`. Triggering it does not require special privilege: any operator running `replay-on-archive` against a subtly corrupted or maliciously tampered backup/archive DB, or any node performing bootstrap verification against a fast-sync snapshot whose checkpoint hash was corrupted, would experience false-positive verification. The comment in the code itself acknowledges this is a known, currently-live gap ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), indicating the authors are aware but the fix has not yet landed.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` between the locally computed output/state and the provided `txn_info` whenever those fields are populated (i.e., at state-checkpoint boundaries), before allowing `verify_execution` or `replay_on_archive` to report success.

### Proof of Concept
Not applicable as a runnable exploit — this is a logic-gap finding in verification code proven by direct code inspection:
1. `ensure_match_transaction_info` never reads `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`. [8](#0-7) 
2. This function is the sole correctness check in `verify_execution` (executor bootstrap/backup verification) and `replay_on_archive` (operator archive-verify tool). [9](#0-8) [5](#0-4) 
3. Feeding a chunk/archive whose stored `TransactionInfo.state_checkpoint_hash` does not match the true root of the write-set-derived state tree (while keeping write-set hash, event hash, gas, and status consistent) will pass both verification routines without error.

**Note:** I could not retrieve the full body of `ensure_transaction_infos_match` in `execution/executor-types/src/ledger_update_output.rs` due to index/content-size limits, so I cannot make a definitive claim about whether the primary state-sync commit path (as opposed to the verification/replay tooling discussed above) is also affected. If confirming that path's exact hash-comparison semantics is important, I'd recommend starting a full Devin session with repository access to inspect that file directly.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L685-706)
```rust
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

**File:** storage/db-tool/src/replay_on_archive.rs (L392-406)
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
        }
```

**File:** execution/executor/src/chunk_executor/chunk_result_verifier.rs (L60-62)
```rust
            // Verify transaction infos match
            ledger_update_output
                .ensure_transaction_infos_match(&self.txn_infos_with_proof.transaction_infos)?;
```
