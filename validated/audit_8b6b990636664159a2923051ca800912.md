## Finding

### Title
Replay-verify comparator never validates the state checkpoint (Sparse Merkle Tree) root hash, allowing a corrupted or tampered archive to pass verification - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the authoritative comparator used by every offline state-integrity tool (`db-tool`'s `replay_on_archive`, `aptos-debugger`, `aptos-move/cli`'s transaction-replay command) to confirm that locally re-executed transaction outputs match the `TransactionInfo` recorded/fetched from a backup or remote source. The function checks status, gas used, write-set hash, and event root hash, but never checks `state_checkpoint_hash` (the Sparse Merkle Tree root summarizing world state) or, when present, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`. As a result these tools can report a transaction (and a whole replayed range) as "verified" even though the actual world-state root diverges from the archived/authenticated data.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  performs four checks — status, gas, write-set hash vs `state_change_hash`, and event root hash — but ends with only a comment: [2](#0-1) 

This TODO explicitly acknowledges that checkpoint hashes are skipped, but the gap is not limited to the trading-native fields it names — `state_checkpoint_hash` (the core world-state SMT root present on `TransactionInfoV0` and `TransactionInfoV1` since genesis) is *never* validated by this function at all, regardless of any feature flag.

This comparator is the sole state-divergence check used by:
- `storage/db-tool/src/replay_on_archive.rs`'s `verify()` / `execute_and_verify()` path, whose entire purpose is to detect divergence between locally executed results and a backed-up/archived ledger [3](#0-2) .
- `execution/executor/src/chunk_executor/mod.rs`'s `verify_execution()`, used for verify-execution mode [4](#0-3) .
- `aptos-move/cli/src/commands.rs`'s transaction-replay commands [5](#0-4) , [6](#0-5) .

None of these callers independently re-check `state_checkpoint_hash` against a locally computed SMT root — they rely entirely on `ensure_match_transaction_info` to certify "TransactionOutput does not match TransactionInfo". Since the write-set hash check only proves the *write set itself* is internally consistent with `state_change_hash`, it says nothing about whether applying that write set on top of the correct base state produces the state root recorded in `state_checkpoint_hash`. A `TransactionInfo` (fetched from an untrusted/compromised backup source, or corrupted in transit/storage) can carry an incorrect `state_checkpoint_hash` and still pass every check in this function as long as gas/status/write-set/events match.

By contrast, the production commit path in `execution/executor/src/chunk_executor/mod.rs::update_ledger()` and `do_state_checkpoint.rs` does properly cross-check computed root hashes against `known_state_checkpoints` derived from `TransactionInfo` [7](#0-6) , [8](#0-7) , so nodes performing normal state-sync/chunk execution are not affected. The exposure is specific to the offline replay-verify/debugging tools that rely on `ensure_match_transaction_info` as their integrity oracle.

### Impact Explanation
`replay_on_archive` and the CLI/debugger replay commands are the tools operators and auditors use to assert that an archived backup or remotely fetched transaction history is authentic and that local execution reproduces the exact same ledger state. Because the state-checkpoint (SMT root) field is never validated, these tools can certify "replay verified successfully" for a chunk whose backup data has a corrupted or malicious `state_checkpoint_hash`, silently masking state divergence in exactly the workflow whose job is to catch it. This directly matches the required scope: "Authenticated API or state-view output bound to the wrong version, object, or proof context" and "restore paths ... must not reinterpret committed data into a different ledger state" — the verification path fails to bind the checked output to the correct proof-bearing root.

### Likelihood Explanation
Any party supplying transaction/backup data consumed by these tools (a compromised/malicious backup store, a corrupted archive, or a modified node serving `get_committed_transaction_at_version`) can trigger this gap without needing any special privilege — the tools are explicitly designed to validate untrusted/external data sources, and the check that should catch tampering of the state root is absent.

### Recommendation
In `ensure_match_transaction_info`, recompute or accept a locally-derived state-checkpoint root (and hot/position checkpoint roots where applicable) and assert equality against `txn_info.state_checkpoint_hash()` (and the V1-only fields) whenever the checkpoint is expected, mirroring the checks already used in the commit path (`known_state_checkpoints` in `do_state_checkpoint.rs`). At minimum, `replay_on_archive`, `aptos-debugger`, and the CLI replay commands should not report success while silently skipping the primary state-root proof.

### Proof of Concept
Not applicable as a runnable exploit — the issue is demonstrated directly by code inspection: the four `ensure!` checks in [9](#0-8)  exhaustively list every field validated, and none of them reference `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, followed by the function returning `Ok(())` unconditionally at [10](#0-9) . Constructing a `TransactionInfo` with a valid `state_change_hash`/`event_root_hash`/`gas_used`/`status` but an arbitrary/incorrect `state_checkpoint_hash`, then calling `ensure_match_transaction_info` with a `TransactionOutput` whose write set/events/gas/status genuinely match, will return `Ok(())` despite the state root being wrong.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L242-293)
```rust
    // Execute the verify one valid range
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
```

**File:** execution/executor/src/chunk_executor/mod.rs (L373-379)
```rust
        let txn_infos = chunk_verifier.transaction_infos();
        let known_state_checkpoints = Some(
            txn_infos
                .iter()
                .map(|t| t.state_checkpoint_hash())
                .collect_vec(),
        );
```

**File:** execution/executor/src/chunk_executor/mod.rs (L692-705)
```rust
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
```

**File:** aptos-move/cli/src/commands.rs (L2651-2655)
```rust
                if !self.skip_comparison {
                    txn_output
                        .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                        .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L44-49)
```rust
        let state_checkpoint_hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_state_checkpoints,
            last_checkpoint.root_hash(),
            "state",
        )?;
```
