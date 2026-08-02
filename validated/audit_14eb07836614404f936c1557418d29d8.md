Based on my investigation, I found a genuine, code-documented state-integrity gap in the transaction-output-to-`TransactionInfo` verification comparator used by replay-based sync/restore paths.

### Title
Missing state-checkpoint-hash verification in `TransactionOutput::ensure_match_transaction_info` allows undetected state-root divergence during chunk replay - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole integrity check used by transaction-replay based verification paths (chunk replayer, replay-verify tooling, debugger) to confirm that a freshly re-executed `TransactionOutput` matches the authenticated `TransactionInfo` stored in the ledger accumulator. The function checks status, gas, write-set hash, and event root hash, but never validates `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` [1](#0-0) .

### Finding Description
The comparator explicitly acknowledges this gap in an inline TODO: it ignores the checkpoint hashes, meaning replay-verify tooling can report success even when the authenticated state/position state root diverges from local execution [2](#0-1) . This function is the only cross-check invoked at each replayed transaction boundary in `storage/db-tool/src/replay_on_archive.rs` (`execute_and_verify`) [3](#0-2) , and it's likewise used by `execution/executor/src/chunk_executor/mod.rs`'s `ReplayChunkVerifier` path for chunk-based transaction replay, and by `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs`. Because none of these call sites separately validate `state_checkpoint_hash` against the locally computed JMT root, a divergence in state-checkpoint computation (e.g., from a VM/state-tree bug, a differing feature-flag interpretation, or hot-state/position-state tree corruption) at a checkpoint boundary would not surface as an error in any of these verification flows — only the write-set hash and event root are checked, not the derived state root itself.

### Impact Explanation
For nodes/tools that rely on transaction replay to reconstruct or validate ledger state (backup replay-verify, chunk-based transaction replayer, debugger-driven re-execution), an actual state-root computation bug elsewhere in the stack would go undetected by this specific gate, allowing a replay-restored or replay-verified database to silently diverge from the authenticated chain state while its own verification reports success. This falls under "committed state that differs from the correct VM result or corrupts durable ledger data" and "hard-fork-only divergence during commit, replay, restore" categories.

### Likelihood Explanation
This is a real, provable, currently-existing gap (not fabricated) — the code's own comment documents that the checkpoint hashes are intentionally excluded from this comparator, and I confirmed no other check fills the gap at any of the four call sites. However, likelihood of exploitation is Medium rather than High/Critical: this check does not gate normal consensus-driven execution/commit (which relies on the accumulator/Merkle proof verification paths I also reviewed, e.g. `types/src/proof/definition.rs`, which correctly verify roots), and there is no evidence of an independent bug that would actually cause the state-checkpoint hash to diverge from a correct VM execution — this gap is a missing safety net, not itself an actively exploitable divergence. The TODO also ties remediation to enabling a currently-disabled feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`), suggesting it is a known, tracked, not-yet-critical-path gap.

### Recommendation
Extend `ensure_match_transaction_info` to independently recompute and compare `state_checkpoint_hash` (and `hot_state_checkpoint_hash`/`position_state_checkpoint_hash` when present) against the values carried in `txn_info`, at every call site that uses this function as a correctness gate (replay-verify tool, chunk replayer, debugger), rather than deferring this to the currently-disabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag.

### Proof of Concept
Not applicable — this is a static code-review finding (missing validation), not a runtime-exploitable input; demonstrating it would require an already-existing state-root-divergence bug elsewhere in execution, which I did not find evidence of in this codebase.

**Caveat**: I was not able to fully trace whether `ReplayChunkVerifier`/chunk-executor replay is reachable from untrusted network input on a mainnet full node (vs. only being invoked by operator-run tooling such as db-restore/db-tool), which materially affects whether this qualifies as "unprivileged." Given the ambiguity and the lack of a demonstrated root-cause divergence, this finding should be treated as a defense-in-depth gap rather than a confirmed exploitable Medium/High vulnerability.

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
