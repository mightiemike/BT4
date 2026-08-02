## Finding

### Title
`ensure_match_transaction_info` skips checkpoint-hash verification, letting replay tooling accept a divergent state root as valid - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticity check that binds an executed `TransactionOutput` to its persisted `TransactionInfo` during replay/verification flows (e.g. `db-tool`'s `replay_on_archive`, and the single-transaction debugger in `aptos-move/cli/src/commands.rs`). It compares status, gas, write-set hash, and event-root hash, but explicitly does **not** compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that exist precisely to authenticate the Merkle-committed state root at a checkpoint. This mirrors the external report's root cause: a required availability/consistency check is missing from the code path that is supposed to gate acceptance, so downstream logic proceeds as if everything validated correctly.

### Finding Description
`TransactionInfo` (V0/V1) carries `state_checkpoint_hash`, and V1 additionally carries `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`, each of which is meant to authenticate a specific Merkle root of durable ledger state at that version. [1](#0-0) 

However, `ensure_match_transaction_info` — the function used to assert that a locally recomputed `TransactionOutput` matches the trusted, previously-committed `TransactionInfo` — only checks status, gas, write-set hash (`state_change_hash`), and `event_root_hash`. It never reads or compares `state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`, and this gap is called out explicitly in a `TODO(trading-native)` comment inside the function itself: [2](#0-1) 

The comment states directly: "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution." [3](#0-2) 

This function is called from the CLI debugger's system-transaction replay path, [4](#0-3) 
and `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::verify`/`execute_and_verify` machinery is built around exactly the same shape of inputs (`expected_txn_infos`, `expected_events`, `expected_writesets`) that this comparator is meant to validate against. [5](#0-4) 

Because the checkpoint-hash fields are skipped, a locally re-executed state tree (state Merkle root, hot-state root, or the newer "position" state root) can silently diverge from the value authenticated in the persisted `TransactionInfo` without the mismatch ever being surfaced — the exact broken invariant this task asks to find: an authenticated proof/root field is not actually checked before the tooling reports success.

### Impact Explanation
This breaks the state-commitment integrity guarantee that replay/verify tooling exists to provide: `replay_on_archive` (used in mainnet backup verification and disaster-recovery/restore validation pipelines) can report "replay succeeded" even though the locally computed state checkpoint root (and, once `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/native-position state roots are enabled, the position-state root) differs from the trusted committed root. That undermines the entire reason replay verification exists — to catch divergence between VM execution and durable committed state — for exactly the checkpoint/root fields that matter most for catching bugs or corruption. In an archive-verification or disaster-recovery context this can mask a genuine state divergence (a corrupted database, a non-deterministic bug, or a hard-fork-only mismatch) as a clean, verified replay.

### Likelihood Explanation
This is not a hypothetical: the gap is deliberately and explicitly documented in-repo (`TODO(trading-native)`), meaning the code path is live and currently shipped in this state, gated only behind not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` functionality for the position-root portion. The `state_checkpoint_hash` skip, however, applies unconditionally today — every call to `ensure_match_transaction_info` currently omits verifying it, regardless of feature flags.

### Recommendation
Extend `ensure_match_transaction_info` to also assert `self`'s locally recomputed checkpoint hash(es) against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()` (V1), and `txn_info.position_state_checkpoint_hash()` (V1) whenever those values are locally computable, before any code path (CLI debugger, `replay_on_archive`, or future callers) is allowed to report a transaction as successfully verified/replayed.

### Proof of Concept
Not applicable as a runnable exploit — the "proof" is the code itself: `ensure_match_transaction_info` at `types/src/transaction/mod.rs:2139-2204` performs exactly four `ensure!` checks (status, gas, write-set hash, event-root hash) and returns `Ok(())` without ever reading `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` from `txn_info`, which is acknowledged directly in the trailing comment at lines 2197-2202. Any caller (e.g. `aptos-move/cli/src/commands.rs:2651-2655`) that relies on this function to validate a checkpoint root will get `Ok(())` even if the locally recomputed checkpoint root differs from the one bound in the trusted `TransactionInfo`.

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

**File:** aptos-move/cli/src/commands.rs (L2651-2655)
```rust
                if !self.skip_comparison {
                    txn_output
                        .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                        .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
                }
```

**File:** storage/db-tool/src/replay_on_archive.rs (L243-313)
```rust
    pub fn verify(&self, start: Version, limit: u64) -> Result<Vec<Error>> {
        let mut total_failed_txns = Vec::with_capacity(limit as usize);
        let txn_iter = self
            .backup_handler
            .get_transaction_iter(start, limit as usize)?;
        let mut cur_txns = Vec::with_capacity(limit as usize);
        let mut cur_persisted_aux_info = Vec::with_capacity(limit as usize);
        let mut expected_events = Vec::with_capacity(limit as usize);
        let mut expected_writesets = Vec::with_capacity(limit as usize);
        let mut expected_txn_infos = Vec::with_capacity(limit as usize);
        let mut chunk_start_version = start;
        let executor = AptosVMBlockExecutor::new();
        for item in txn_iter {
            // timeout check
            if let Some(duration) = self.timeout_secs {
                if self.replay_stat.get_elapsed_secs() >= duration {
                    bail!(
                        "Verify timeout: {}s elapsed. Deadline: {}s. Failed txns count: {}",
                        self.replay_stat.get_elapsed_secs(),
                        duration,
                        total_failed_txns.len(),
                    );
                }
            }

            let (
                input_txn,
                persisted_aux_info,
                expected_txn_info,
                expected_event,
                expected_writeset,
            ) = item?;
            let is_epoch_ending = expected_event.iter().any(ContractEvent::is_new_epoch_event);
            cur_txns.push(input_txn);
            cur_persisted_aux_info.push(persisted_aux_info);
            expected_txn_infos.push(expected_txn_info);
            expected_events.push(expected_event);
            expected_writesets.push(expected_writeset);
            if is_epoch_ending || cur_txns.len() >= self.chunk_size {
                let cnt = cur_txns.len();
                while !cur_txns.is_empty() {
                    // verify results
                    let failed_txn_opt = self.execute_and_verify(
                        &executor,
                        &mut chunk_start_version,
                        &mut cur_txns,
                        &mut cur_persisted_aux_info,
                        &mut expected_txn_infos,
                        &mut expected_events,
                        &mut expected_writesets,
                    )?;
                    // collect failed transactions
                    total_failed_txns.extend(failed_txn_opt);
                }
                self.replay_stat.update_cnt(cnt as u64);
                self.replay_stat.print_tps();
            }
        }
        // verify results
        let fail_txns = self.execute_and_verify(
            &executor,
            &mut chunk_start_version,
            &mut cur_txns,
            &mut cur_persisted_aux_info,
            &mut expected_txn_infos,
            &mut expected_events,
            &mut expected_writesets,
        )?;
        total_failed_txns.extend(fail_txns);
        Ok(total_failed_txns)
    }
```
