### Title
`ensure_match_transaction_info` skips checkpoint-hash comparison, allowing replay-verification to accept a divergent authenticated state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` (used by `db-tool`'s `replay_on_archive` and by `ChunkExecutor::verify_execution` in "verify-execution" replay mode) checks status, gas, write-set hash, and event-root hash against the authenticated `TransactionInfo`, but deliberately skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`. This is called out by the developers themselves in a `TODO(trading-native)` comment.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates a locally re-executed `TransactionOutput` against a `TransactionInfo` that was authenticated via an accumulator/ledger-info proof (e.g. fetched from a backup or from an archival node). It checks:
- transaction status [2](#0-1) 
- gas used [3](#0-2) 
- write-set hash vs `state_change_hash` [4](#0-3) 
- event-root hash [5](#0-4) 

But it explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, which are also fields committed into `TransactionInfoV0`/`TransactionInfoV1` and covered by the accumulator proof [6](#0-5) . The `TransactionInfoV1` struct carries these fields explicitly [7](#0-6) .

This function is invoked as the sole correctness gate in two important integrity-verification tools:
- `execution/executor/src/chunk_executor/mod.rs::verify_execution`, used when replaying a chunk to independently verify that local execution matches the archived/authenticated `TransactionInfo` [8](#0-7) .
- `storage/db-tool/src/replay_on_archive.rs::execute_and_verify`, the primary tool operators use to detect state divergence when replaying archived history against a debugger execution [9](#0-8) .
- `aptos-move/aptos-debugger` and the CLI's transaction-replay command also rely on it as the pass/fail signal for a replay [10](#0-9) .

Because the state/hot-state/position checkpoint hashes are skipped, a local re-execution whose Sparse-Merkle-Tree root (the state committed at a checkpoint boundary) diverges from the authenticated on-chain root can still be reported as "match" by these verification tools.

### Impact Explanation
This does not corrupt what's actually committed to the DB by the normal execution/commit path — the checkpoint hash itself is still computed and stored via `do_state_checkpoint.rs`, and the primary consensus/execution pipeline is unaffected. The impact is limited to a **replay-verification / anti-corruption tooling gap**: `replay_on_archive` and `verify_execution` (state-sync's replay-mode integrity check, and the primary tool for auditing archived history) can silently pass over a state-root divergence, since checkpoint hash equality is the one signal that would catch a bug in state-view construction, checkpoint hashing, or an unnoticed consensus-breaking VM change. This weakens the state-integrity guarantee that "committed state that differs from the correct VM result" would be caught, per the report's own required-impacts language. The comment confirms this is a known, currently-latent gap gated on an unlaunched feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`), so it is presently exercised only for the legacy state-checkpoint/hot-state-checkpoint hash fields (which already exist in mainnet `TransactionInfo`s today), not just the future "trading native" feature.

### Likelihood Explanation
This is a real, already-merged behavior (not merely a future flag-gated code path) since `state_checkpoint_hash` and `hot_state_checkpoint_hash` exist and are populated today. Any divergence in the state-checkpoint hash computation between the authenticated data and a re-execution would go undetected by `ensure_match_transaction_info`, whereas the surrounding proof/root-hash verification code elsewhere in the codebase (accumulator/Merkle proofs reviewed in `types/src/proof/definition.rs`) is consistently rigorous. I could not verify whether any other independent code path (e.g., full accumulator reconstruction from re-computed `TransactionInfo.hash()`) redundantly re-validates the checkpoint hash during `verify_execution`/`replay_on_archive` — I did not find one in the paths traced, but did not exhaustively check every downstream commit path.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally recomputed checkpoint hashes (when available/applicable to the transaction), consistent with the TODO comment, before any feature depending on Merkle-root parity (e.g. `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is enabled, and ideally regardless of that flag, since the legacy state-checkpoint hash is already a security-relevant, currently-produced field.

### Proof of Concept
Conceptual, not exploit code (this is a detection-tooling gap rather than a directly attacker-triggerable state corruption):
1. Run `replay_on_archive` (or `db-tool` replay-verify / `ChunkExecutor::verify_execution`) against a range of transactions where the *authenticated* `TransactionInfo.state_checkpoint_hash` differs from what local re-execution's state view would produce (e.g., due to a bug in state-view construction, pruning-window edge case, or non-consensus-breaking-but-incorrect state application) while status/gas/write-set-hash/event-root-hash still match.
2. `ensure_match_transaction_info` returns `Ok(())` because it only checks status/gas/write-set-hash/event-root-hash.
3. `execute_and_verify` in `replay_on_archive.rs` [11](#0-10)  and `verify_execution` in the chunk executor [12](#0-11)  treat this as a successful, verified match, so the operator/tooling never surfaces the state-root divergence.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L388-405)
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
```

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```
