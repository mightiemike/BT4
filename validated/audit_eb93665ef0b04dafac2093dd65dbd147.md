Found a solid analog. The `ensure_match_transaction_info` function in `types/src/transaction/mod.rs` — used specifically by the replay-verification tool `storage/db-tool/src/replay_on_archive.rs` — deliberately skips comparing the state checkpoint hashes, allowing archived `TransactionInfo` records with wrong state roots to pass "verification" as if correct.

### Title
Replay-verify (`replay_on_archive`) accepts backup `TransactionInfo` with a wrong/tampered state root as valid - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole per-transaction correctness check used by the `replay_on_archive` db-tool to confirm that a locally re-executed transaction output matches the historical, ledger-info-authenticated `TransactionInfo` pulled from a backup/archive. The function only checks `status`, `gas_used`, `write_set_hash` (`state_change_hash`), and `event_root_hash`. It explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents with a `TODO`. As a result, an archived/backup transaction stream whose checkpoint state root diverges from what local re-execution actually produces will be reported as a successful, verified replay.

### Finding Description
`ensure_match_transaction_info` performs these checks only: [1](#0-0) 

The comment immediately following it explains the omission is known and unaddressed: [2](#0-1) 

This function is invoked as the definitive correctness check in `replay_on_archive.rs`'s `execute_and_verify`, which drives the entire `db-tool replay-verify` workflow: it loads `expected_txn_info` from a backup, re-executes the transactions locally via `AptosVMBlockExecutor`, and calls `ensure_match_transaction_info` to decide pass/fail: [3](#0-2) 

Because the comparator never inspects `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`, any divergence in the JMT/state-checkpoint root — whether from a backup-generation bug, bit-corruption of the manifest/proof files, a non-determinism bug in state-checkpoint computation (e.g. in the position-state or hot-state summary paths under `execution/executor/src/workflow/do_state_checkpoint.rs`), or outright malicious archive tampering upstream of `replay_on_archive` — is invisible to this tool. Note that the state root is exactly what's cryptographically authenticated via `TransactionInfo::hash()` under the accumulator/`LedgerInfoWithSignatures`, so this is a proof-relevant field, not incidental data.

By contrast, the analogous "known-hash validation" path used during actual online execution/state-sync (`get_state_checkpoint_hashes` in `do_state_checkpoint.rs`) does compare against `known_*_state_checkpoints` for main/hot/position state: [4](#0-3) 

showing the codebase does have the concept and capability of validating these root hashes — it's simply omitted from the replay-verify comparator used against backup data.

### Impact Explanation
`replay_on_archive`/`replay-verify` exists specifically to give operators, auditors, and node operators confidence that a historical backup/archive is faithful to the actual chain state (used e.g. to validate archive integrity before using it for state-sync/bootstrap, or for auditing purposes). With this gap, a backup whose `state_checkpoint_hash` (or `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) has been corrupted or diverges from the real, re-executed state will still be reported as "verified" — a false assurance of state integrity. Any downstream process trusting a "replay-verify passed" result (state-sync bootstrapping from that archive, backup integrity audits, dispute resolution) would incorrectly treat a wrong ledger state as authenticated/correct. This is a durable, silent state-root-authentication gap rather than a transient computation bug, meeting the "authenticated API/proof-bearing response accepted as valid despite corruption" bar.

### Likelihood Explanation
This does not require any privileged access to trigger on the verifier side — it happens automatically whenever `replay_on_archive` is run against any backup/archive with a corrupted or subtly wrong checkpoint hash (bit flip, storage backend fault, incomplete/buggy backup writer, or bug in state-checkpoint calculation such as position-state summary logic). The comment in the code confirms this is a known, currently-live gap ("so replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution"), i.e. it's not a hypothetical — the maintainers have already identified this exact failure mode in-repo but have not fixed it.

### Recommendation
Extend `ensure_match_transaction_info` (or the `replay_on_archive` verification call site) to compute the actual post-execution state/hot-state/position-state checkpoint hashes for transactions that are checkpoints, and compare them against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()`, failing verification on mismatch just as is already done in the live `do_state_checkpoint.rs` "known-hash validation" path.

### Proof of Concept
1. Take any valid transaction backup/archive (manifest + proof + `TransactionInfo` list) usable by `db-tool replay-verify`.
2. Corrupt the `state_checkpoint_hash` (or `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) field of one `TransactionInfo` entry that is a checkpoint (this field is still covered by the accumulator leaf hash in principle, but the tool never recomputes/compares it against local execution — it only trusts what's read from the backup file as "expected").
3. Run `replay_on_archive` (`cargo run -p aptos-db-tool -- replay-on-archive ...`) against this backup with a local DB / VM re-execution of the same range.
4. Observe `execute_and_verify` → `ensure_match_transaction_info` returns `Ok(())` (no error) for that transaction because it never inspects `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, despite the local re-executed state root differing from the (corrupted) recorded value.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L179-190)
```rust
        // Per-tx hash vector + known-hash validation (shared with main/hot state).
        let hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_position_state_checkpoints,
            new_last_checkpoint.root_hash(),
            "position_state",
        )?;

        let summary =
            LedgerWithSummary::from_latest_and_last_checkpoint(new_latest, new_last_checkpoint);
        Ok((summary, hashes))
    }
```
