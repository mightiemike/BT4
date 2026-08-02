### Title
`ensure_match_transaction_info` skips checking `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, letting replay-verify and chunk-execution verification accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single authenticated-equivalence check used by both the chunk executor's execution-verification path and the `db-tool replay-on-archive` tool to confirm that locally re-executed transaction outputs match the `TransactionInfo` recorded/authenticated on-chain. The function checks status, gas, write-set hash, and event root hash, but the code itself contains a `TODO` acknowledging it does **not** check the state checkpoint hashes bound into `TransactionInfo`.

### Finding Description
`ensure_match_transaction_info` builds `expected_txn_status`, compares `gas_used`, recomputes `write_set_hash` and `event_root_hash`, and asserts equality against the values embedded in `txn_info` (a `TransactionInfo` that is itself authenticated by a ledger-info-signed accumulator proof in the callers). It never reads or compares `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` against anything derived from local re-execution, as flagged directly in the code: [1](#0-0) 

This function is the sole verification performed by:
- `ChunkExecutor::verify_execution`, which re-executes transactions and calls `ensure_match_transaction_info` per transaction to validate a chunk against the backup/state-sync-provided `TransactionInfo`s and write sets: [2](#0-1) 
- `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which is the authenticated integrity tool operators run to confirm archive-node replay matches the historical, ledger-info-proven `TransactionInfo`s: [3](#0-2) 

Because state-checkpoint hashes (which commit to the post-transaction Merkle/hot-state root, i.e., the actual committed ledger state) are excluded from this comparator, a divergence between the locally computed state root and the on-chain-authenticated `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` in `TransactionInfo` will **not** be detected by either the chunk-executor's execution-verification mode or the `replay_on_archive` tool. Both tools will report "verification succeeded" even though the state root produced by local execution differs from the authenticated on-chain root for that version.

### Impact Explanation
This breaks the "authenticated API/verification output bound to the correct root" invariant explicitly called out in the state-integrity gate. `replay_on_archive` and chunk-executor `verify_execution` are the exact tools relied upon to detect state divergence (e.g., non-determinism bugs, hidden state corruption, or hard-fork-triggering execution differences) between a node's local VM execution and the network's canonical, ledger-info-signed state. If the state root diverges (for any reason — a VM bug, an incorrectly applied write, hot-state/position tree corruption) the verification will pass and silently mask the divergence, which is precisely the “hard-fork-only divergence during commit, replay, restore, or proof verification” and “committed state that differs from the correct VM result” impact class this task targets. This is a high-severity gap in the ledger's self-verification tooling rather than a network-exploitable bypass, since normal consensus/execution commit paths (`do_state_checkpoint.rs`) independently compute and validate checkpoint hashes during block execution; the affected paths are specifically the standalone verification tools (`replay_on_archive`, chunk-executor `verify_execution`) that are supposed to be the last line of defense for detecting exactly this kind of divergence.

### Likelihood Explanation
The gap is unconditional and always reachable whenever `verify_execution` or `replay_on_archive` are run — no attacker input or privilege is required to trigger the missing check; it simply never executes regardless of the actual data. The likelihood of an actual state-root divergence occurring is separate (would come from a VM/execution bug elsewhere), but given such a divergence exists, both tools are guaranteed to miss it because the comparator statically omits these fields, as the in-code TODO comment confirms.

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally computed equivalents (analogous to how `write_set_hash` and `event_root_hash` are already checked), gating on `COMPUTE_TRADING_NATIVE_STATE_ROOTS` if that feature governs when these fields are populated. This must be completed before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in production, as the comment itself warns.

### Proof of Concept
Not independently exploitable via user transactions; the flaw is a missing assertion in verification tooling. To observe it: construct/replay a transaction sequence where local re-execution's resulting state (JMT or hot-state) root differs from the `state_checkpoint_hash`/`hot_state_checkpoint_hash` carried in the authenticated `TransactionInfo`, while all other fields (status, gas, write set, events) coincide (or run under `COMPUTE_TRADING_NATIVE_STATE_ROOTS` where a bug diverges only the position/hot-state tree). Run `db-tool replay-on-archive` or `ChunkExecutor::verify_execution` over that range; both will return `Ok`, i.e., "replay verified," despite the state root mismatch, because `ensure_match_transaction_info` never inspects those fields. [4](#0-3)

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
