This is confirmed: `execution/executor/src/chunk_executor/mod.rs` also calls `ensure_match_transaction_info` (grep matched, though I couldn't fetch its surrounding content — index limit), and `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify` at lines 388-406 relies on it as the sole correctness gate when re-executing archived transactions against locally re-executed VM output.

### Title
`TransactionOutput::ensure_match_transaction_info` skips state/hot-state/position checkpoint hash validation, letting replay-verify accept divergent state roots - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated check used by replay/verification tooling to confirm that a freshly re-executed `TransactionOutput` matches the trusted, already-committed `TransactionInfo` (which is itself bound into the transaction accumulator and ledger-info signature). The function checks status, gas used, write-set hash, and event-root hash, but explicitly, by its own inline comment, omits comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`.

### Finding Description [1](#0-0) 

The comment at lines 2197-2202 states plainly:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```
This is used in `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` [2](#0-1)  where a chunk of archived transactions is re-executed via `AptosVMBlockExecutor::execute_block` and the resulting `TransactionOutput`s are checked only via `ensure_match_transaction_info` against the archived `TransactionInfo`. It is also referenced in `execution/executor/src/chunk_executor/mod.rs` (confirmed via grep match, though I could not retrieve the surrounding code due to index size limits, so I cannot fully verify how the chunk executor uses the result there).

Because the comparator never checks `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`, a state root computed by the local JMT/state view can silently diverge from the state root that was actually signed into the historical `LedgerInfoWithSignatures` for that version, and `ensure_match_transaction_info` will still return `Ok(())`.

### Impact Explanation
This breaks the "authenticated API / proof-bearing tooling stays bound to the right root" invariant for `replay_on_archive`: it is the exact tool operators use to validate that an archive/replica reconstructs the canonical, validator-signed ledger state. If the state (or hot-state / position-state) computation diverges — due to a bug in state materialization, a JMT/hot-state bug, or corrupted archive data — this tool will still report "replay succeeded," masking a state-commitment divergence that would otherwise be caught. This matches the required impact class: "Hard-fork-only divergence during commit, replay, or proof verification" and "wrong ... state proof accepted as valid," because the omitted fields are precisely the ones bound into `TransactionInfo` and therefore into the accumulator root that consensus signs.

### Likelihood Explanation
The gap is unconditional in current code (not gated behind the not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature) — it silently affects every replay-verify run today, for any transaction whose write set/events happen to match but whose state root diverges (e.g., due to any bug elsewhere in state computation, hot-state materialization, or position-state tracking). The likelihood of the underlying state divergence occurring is separate from this bug; this specific flaw guarantees that if such a divergence occurs, existing verification tooling will fail to detect it, defeating its entire purpose.

### Recommendation
In `ensure_match_transaction_info`, add comparisons for `self`-derived checkpoint hashes (or the ones passed in) against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()` (V1 only), and `txn_info.position_state_checkpoint_hash()` (V1 only) whenever the corresponding value is computable for the transaction, before considering the check successful, as the existing TODO comment already recommends.

### Proof of Concept
Not independently reproducible from index alone: constructing an actual PoC requires an environment where a re-executed `TransactionOutput`'s underlying state computation diverges from the archived committed state root while producing an identical write-set hash and event-root hash for that version. I could not verify this locally with build/test execution (no filesystem/terminal access in ask-only mode), so this should be validated by a maintainer/agent with repo access, e.g. by writing a unit test that constructs two `TransactionInfo`s differing only in `state_checkpoint_hash`, producing a `TransactionOutput` whose write set matches one but not the other, and confirming `ensure_match_transaction_info` incorrectly returns `Ok(())`.

**Note on completeness:** Due to index size limits, I could not retrieve the full contents of `execution/executor/src/chunk_executor/mod.rs` to confirm precisely how it uses `ensure_match_transaction_info` (only a grep match was found). This should be checked by a Devin session with full filesystem access before finalizing severity/scope, since that call site may have additional independent checks that partially mitigate the gap in the chunk-execution path (as opposed to the `replay_on_archive` CLI tool, which I did fully verify).

### Citations

**File:** types/src/transaction/mod.rs (L2139-2203)
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
