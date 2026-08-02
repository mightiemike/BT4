Confirmed: `replay_on_archive.rs`'s `execute_and_verify` (used by `db-tool replay-on-archive`, the tool operators run to verify archived ledger history, including post-hard-fork audits) relies solely on `ensure_match_transaction_info` at [1](#0-0)  to decide pass/fail for each replayed transaction, and that comparator never checks `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` as the code itself documents at [2](#0-1) .

### Title
Replay-verify tooling accepts divergent state/position Merkle roots because `ensure_match_transaction_info` never checks checkpoint hashes - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole correctness oracle used by `db-tool replay-on-archive` and `aptos-debugger`/CLI replay to decide whether a locally re-executed transaction matches the authenticated, on-chain `TransactionInfo`. It checks status, gas used, write-set hash, and event root hash, but it never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the actual authenticated JMT/state-summary roots carried in `TransactionInfoV1`/`V0`. A transaction whose write set (and thus `write_set_hash`) matches but whose resulting state Merkle root (or hot-state/position root) diverges from the authenticated chain value will be reported as a successful, matching replay.

### Finding Description
`ensure_match_transaction_info` at [3](#0-2)  validates:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set_hash` vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

It ends with an explicit developer acknowledgment at [4](#0-3)  that the checkpoint hashes (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) are not validated, meaning "replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution."

This is the exact opposite situation from the core consensus/chunk-executor commit path, which *does* validate computed checkpoint roots against `known_state_checkpoints`/`known_hot_state_checkpoints`/`known_position_state_checkpoints` inside `DoStateCheckpoint::get_state_checkpoint_hashes` ( [5](#0-4) ), so live consensus commit is not affected. The gap is isolated to the independent verification tools that operators/auditors rely on to detect ledger corruption or divergence after the fact (e.g., across upgrades or forks):
- `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify` treats `ensure_match_transaction_info` returning `Ok(())` as full confirmation of a correct replay: [1](#0-0) .
- `aptos-move/cli/src/commands.rs` uses the same call to gate whether a locally replayed transaction "matches": [6](#0-5) .

Because `write_set_hash` only hashes the flat `WriteSet` produced by this one transaction, it cannot detect corruption that depends on the *order/composition* of prior state (i.e., a wrong resulting Jellyfish Merkle root, wrong hot-state root, or wrong native-position root) as long as the write set itself matches byte-for-byte. Any divergence introduced upstream — buggy JMT update logic, a stale/incorrectly-restored state base, or corrupted state-merklization on the machine performing verification — would be silently accepted.

### Impact Explanation
`replay-on-archive`/replay-verify is the audited-history integrity backstop: it is the mechanism by which node operators and Aptos Labs itself validate that archived/restored ledger data and its authenticated proofs are self-consistent, particularly across hard forks, restores, and upgrades. Because the comparator omits the state/hot-state/position checkpoint hash checks, a corrupted or diverged state root (e.g., from a bug in JMT update, state-restore, or the experimental native-position/trading feature) would not be flagged by this tool even though the write set matched. This directly maps to the "proof-verification" and "restore/replay" integrity pivots called out in scope: an authenticated root (state checkpoint hash) is effectively never checked by the tool whose entire purpose is to check it.

### Likelihood Explanation
The core consensus commit path already independently re-derives and checks these roots (`DoStateCheckpoint`), so this does not create a live consensus-fork risk by itself. However, the bug is deterministic and always triggers for every checkpoint transaction replayed via `replay-on-archive`/CLI/aptos-debugger — the checkpoint hash fields are simply never read, not verified. It would surface (and matter) precisely when the tool is most needed: after a hard fork, a JMT bug, or a botched state restore, i.e., exactly the scenarios operators use this tool to catch.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `self`/execution-derived checkpoint roots against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` whenever the transaction/output actually produced a checkpoint (mirroring the logic already implemented in `DoStateCheckpoint::get_state_checkpoint_hashes`), and update all three callers (`replay_on_archive.rs`, CLI `commands.rs`, and `aptos_debugger.rs`) to pass through the locally-computed checkpoint hash so it is actually checked rather than silently ignored.

### Proof of Concept
1. Run `aptos-db-tool replay-on-archive --start-version V --end-version V` against an archive where transaction `V` is a checkpoint transaction.
2. Locally patch/introduce a state-merklization discrepancy that does not change the write set bytes for transaction `V` but does change the resulting JMT root (e.g., a JMT node-key/version regression, or corrupted restored snapshot at genesis of the range) — write_set_hash, event_root_hash, status, and gas_used remain identical to the authenticated `TransactionInfo`.
3. Observe `execute_and_verify` returns `Ok(None)` (no error) at [1](#0-0) , i.e., the tool reports the transaction as successfully replayed/verified despite the authenticated `state_checkpoint_hash` differing from the actually-computed root, because `ensure_match_transaction_info` never reads that field.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L192-234)
```rust
    fn get_state_checkpoint_hashes(
        execution_output: &ExecutionOutput,
        known_state_checkpoints: Option<Vec<Option<HashValue>>>,
        computed_last_checkpoint_hash: HashValue,
        label: &str,
    ) -> Result<Vec<Option<HashValue>>> {
        let _timer = OTHER_TIMERS.timer_with(&[&format!("get_{label}_checkpoint_hashes")]);

        let num_txns = execution_output.to_commit.len();
        let last_checkpoint_index = execution_output
            .to_commit
            .state_update_refs()
            .last_inner_checkpoint_index();

        if let Some(known) = known_state_checkpoints {
            ensure!(
                known.len() == num_txns,
                "Bad number of known {label} hashes. {} vs {}",
                known.len(),
                num_txns,
            );
            if let Some(idx) = last_checkpoint_index {
                ensure!(
                    known[idx] == Some(computed_last_checkpoint_hash),
                    "{label} root hash mismatch with known hashes passed in. {:?} vs {:?}",
                    known[idx],
                    Some(computed_last_checkpoint_hash),
                );
            }
            Ok(known)
        } else {
            if !execution_output.is_block {
                // We should enter this branch only in test.
                execution_output.to_commit.ensure_at_most_one_checkpoint()?;
            }

            let mut out = vec![None; num_txns];
            if let Some(index) = last_checkpoint_index {
                out[index] = Some(computed_last_checkpoint_hash);
            }
            Ok(out)
        }
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
