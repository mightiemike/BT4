Based on the investigation, I found a genuine local integrity gap analogous to the reported bug class ("a validation function whose name/contract implies it checks X, but the code silently skips checking X").

### Title
Replay/verify-execution tooling silently ignores state Merkle root mismatches (`state_checkpoint_hash`) - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole comparator used by state-integrity replay tooling to confirm that a locally re-executed transaction matches the authoritative, previously-committed `TransactionInfo`. Despite its purpose — confirming the replayed result reproduces the exact committed ledger state — it never compares the state-checkpoint (JMT) root hash, so a divergence in the actual committed state root goes completely undetected by this check.

### Finding Description
`ensure_match_transaction_info` compares only: execution status, gas used, write-set hash (`state_change_hash`), and event root hash. [1](#0-0) 

It never checks `txn_info.state_checkpoint_hash()` (nor `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()` on `TransactionInfoV1`), even though the function's own comment acknowledges the gap: [2](#0-1) 

This function is the *only* verification step used by:
- `ChunkExecutorInner::verify_execution`, which drives `--verify-execution` chunk replay and is expected to catch execution-result divergence during state sync/replay: [3](#0-2) 
- `db-tool`'s `replay_on_archive::execute_and_verify`, the dedicated archive replay-verification tool whose entire job is to detect state divergence between re-execution and the historical/authoritative chain: [4](#0-3) 

The write-set hash equality does guarantee the *delta* is identical, but it does not guarantee the resulting Jellyfish Merkle Tree root is identical — the state-checkpoint construction pipeline (batching, sharding, hot-state/position-state root derivation) is a separate code path from write-set production and can diverge independently (this is exactly the class of bug the code comment calls out: "the authenticated position state root diverges from local execution"). Because the comparator never checks `state_checkpoint_hash`, such a divergence is invisible to both `verify_execution` mode and `replay_on_archive`.

### Impact Explanation
This falls squarely within the required "Hard-fork-only divergence during commit, replay, restore, or proof verification" impact category. Replay-verify/`verify_execution` tooling exists specifically to catch bugs where re-execution/replay produces a different ledger state (state root) than what was actually committed on-chain — the canonical way such bugs are caught before they cause a network-wide consensus divergence (hard fork). With this gap, a bug in the state-checkpoint/Merkle-tree construction logic (independent of write-set serialization) can silently pass every replay-verify and verify-execution run, i.e., the very tooling meant to be the last line of defense against undetected state-root divergence gives a false "match" signal.

### Likelihood Explanation
This is not a hypothetical parsing gap — it's a self-documented omission in code that ships and runs by default whenever `ensure_match_transaction_info` is invoked (both call sites shown above use it unconditionally, with no additional state-root check elsewhere in either flow). Any bug that affects state-root computation without affecting write-set hashing (plausible given `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` are computed via distinct tree-construction code) will go undetected by the two primary replay/verification tools.

### Recommendation
Add explicit checks in `ensure_match_transaction_info` comparing `self`'s freshly computed state-checkpoint hash(es) — main state, hot state, and position state, as applicable to the `TransactionInfo` variant — against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` before declaring a match, rather than leaving this gated behind an unimplemented `COMPUTE_TRADING_NATIVE_STATE_ROOTS` TODO.

### Proof of Concept
Not independently reproducible via static analysis alone — a working PoC would require constructing a transaction whose committed write set matches the archived write set/hash exactly, but whose resulting Jellyfish Merkle Tree root differs from the archived `state_checkpoint_hash` (e.g., via a bug in hot-state or sharded-JMT batching). I could not execute or fuzz this locally to confirm such a divergence is currently reachable; this is flagged as **uncertain** and would require a Devin session with execution/test access to construct and confirm a concrete divergent-state scenario.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2196)
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

```

**File:** types/src/transaction/mod.rs (L2197-2204)
```rust
        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
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
