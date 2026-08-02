### Title
Replay-verify tooling silently accepts execution results with a diverging state-checkpoint root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` — the routine used by `db-tool replay-on-archive`, `aptos-debugger`, and the CLI to confirm that re-executing archived transactions locally reproduces the state that was actually committed on-chain — never compares the locally-recomputed state-checkpoint root against the `state_checkpoint_hash` (or `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) carried in the trusted `TransactionInfo`. Only `status`, `gas_used`, `write_set` hash, and `event_root_hash` are checked.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  is documented as verifying that a locally produced `TransactionOutput` matches an authenticated `TransactionInfo` retrieved from archive/backup storage. It checks:
- execution status [2](#0-1) 
- gas used [3](#0-2) 
- write-set hash vs `state_change_hash` [4](#0-3) 
- event root hash [5](#0-4) 

It never touches `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. The code itself acknowledges this gap: [6](#0-5) 

This function is the sole verification gate in `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes archived transactions and calls `ensure_match_transaction_info` per-transaction as the pass/fail criterion for the whole replay-verify job: [7](#0-6) 

Because the comparator ignores the checkpoint-hash fields entirely (not only when trading-native roots are enabled — the state/hot-state checkpoint hash is never checked at all, regardless of feature flags), a divergence in the computed Sparse-Merkle/Jellyfish-Merkle state root at a checkpoint boundary — caused by a state-computation bug, an execution non-determinism, or corruption introduced elsewhere in the write-set → storage pipeline — would go completely undetected by this tool. The write-set hash check only proves the *raw write-set bytes* match; it does not prove that applying those writes onto the parent state actually reproduces the *state root* that was consensus-committed and archived.

### Impact Explanation
`replay_on_archive` and the `aptos-debugger`/CLI replay-verify flows are the primary tooling used to certify that a new binary/VM produces bit-identical ledger state before a mainnet upgrade, and to detect execution non-determinism across historical ranges (this is precisely the "Hard-fork-only divergence during ... replay ... or proof verification" category called out as in-scope). Because the state-checkpoint root is never validated, this tool can report a clean, fully-passing replay-verify run over a version range even though the locally-computed state tree root at checkpoints has silently diverged from the authenticated, consensus-committed root. This defeats the entire purpose of the tool as a pre-upgrade safety net and could let a state-corrupting bug or non-deterministic execution path ship to validators undetected, since the only automated cross-check available for this class of regression is bypassed.

### Likelihood Explanation
No special privileges or malicious input are required — this triggers automatically whenever `replay_on_archive` (or the debugger/CLI paths that call `ensure_match_transaction_info`) is run against any archive segment where state-root computation diverges (e.g., an unrelated JMT/state-view bug or a bug in a not-yet-broadly-tested feature such as the `TRADING_NATIVE`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS`/hot-state paths currently in this codebase). The gap is unconditional in the current code — it is not gated behind any feature flag being off; the checkpoint-hash comparison simply does not exist in the function for any code path.

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash` (recomputing/obtaining the local state-checkpoint root for transactions where `has_state_checkpoint_hash()` is true), and, once those features are exercised in replay/verify tooling, `hot_state_checkpoint_hash` and `position_state_checkpoint_hash` as well. The TODO already present in the code identifies exactly this required change; it should be completed and enforced before relying on `replay_on_archive`/debugger-based replay-verify results as a correctness gate for consensus-facing changes.

### Proof of Concept
Not directly exploitable by an external attacker against mainnet consensus (the consensus commit path in `do_ledger_update.rs`/`do_state_checkpoint.rs` independently computes and includes the checkpoint hash in the accumulator, so a bad actor cannot forge a checkpoint hash there). The concrete, locally provable defect is a logic/coverage gap:
1. Construct (or use existing test helpers such as `storage/aptosdb/src/db/aptosdb_test.rs` `test_get_latest_ledger_summary`) a `TransactionInfo` with a correct `state_checkpoint_hash` and a `TransactionOutput` whose write-set hashes identically to `state_change_hash`/events but which, when actually applied to the parent state view, produces a different state root (e.g., inject a non-deterministic value that doesn't affect write-set bytes but affects how the SMT is populated, or simply pass a mismatched/incorrect `state_checkpoint_hash` argument).
2. Call `output.ensure_match_transaction_info(version, &txn_info, ..)` — per [1](#0-0) , it returns `Ok(())` because none of its checks reference `state_checkpoint_hash`.
3. Running this through `replay_on_archive.rs`'s `execute_and_verify` [7](#0-6)  likewise reports success (no entry added to `total_failed_txns`) despite the state-root divergence.

I was unable to run this scenario directly in a live environment (no execution/tooling access here); the analysis is based on static tracing of `ensure_match_transaction_info`'s exact comparison logic and its unique call sites in `replay_on_archive.rs`, `aptos-debugger`, and the CLI, all confirmed via `grep_search`.

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
