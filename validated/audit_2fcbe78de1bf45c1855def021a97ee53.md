### Title
`ensure_match_transaction_info` silently skips state-checkpoint hash verification, letting a diverged state root pass replay/execution verification - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the invariant used by replay/verify tooling (`ChunkExecutorInner::verify_execution` in `execution/executor/src/chunk_executor/mod.rs`, `aptos-debugger`'s `print_mismatches`, and the CLI replay command in `aptos-move/cli/src/commands.rs`) to prove that locally re-executed `TransactionOutput`s match the authenticated `TransactionInfo` obtained from a transaction-accumulator proof. The function checks status, gas used, write-set hash, and event root hash, but it never checks `state_checkpoint_hash()` (nor `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`), even though the code that produces the checkpoint hash for comparison already exists elsewhere in the pipeline (`assemble_transaction_infos` in `execution/executor/src/workflow/do_ledger_update.rs`).

### Finding Description
`ensure_match_transaction_info` is analogous to `utilizationRate()` in the seed report: it is a bounded/complete-looking validation function that is supposed to guarantee an invariant (locally computed transaction result == authenticated result) but has a known, unhandled gap that lets an out-of-invariant value through silently. [1](#0-0) 

The function explicitly documents the gap in its own body:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```
It checks `status`, `gas_used`, `write_set_hash` (== `state_change_hash`), and `event_root_hash`, but returns `Ok(())` without ever comparing `txn_info.state_checkpoint_hash()` against the locally-computed state Sparse-Merkle-Tree root that is produced during state-checkpoint construction (`do_state_checkpoint.rs`, `assemble_transaction_infos` in `do_ledger_update.rs`). This is exactly the same class of bug as the seed report: a value that is supposed to be validated/bounded (here: "the produced state root equals the authenticated one") is not checked in every code path where the enclosing formula assumes it, so a divergent value passes through as if valid.

This function is used by:
- `ChunkExecutorInner::verify_execution` (`execution/executor/src/chunk_executor/mod.rs:648-708`), which is exercised by state-sync's "execute and verify" bootstrapping mode and by `db-tool`'s `replay_verify`/`replay_on_archive` commands. [2](#0-1) 
- `aptos-debugger`'s mismatch reporting (`aptos-move/aptos-debugger/src/aptos_debugger.rs:233-246`). [3](#0-2) 
- The CLI replay command (`aptos-move/cli/src/commands.rs:2797-2813`). [4](#0-3) 

### Impact Explanation
Because `state_checkpoint_hash` (and, once enabled, `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) is never compared, a local execution result whose write-set hash, gas, status and event root all match the authenticated `TransactionInfo` but whose resulting global-state Merkle root diverges (e.g., due to a state-store bug, a non-deterministic native, or storage corruption during the state-checkpoint stage) will be reported by replay/verify tooling as a *successful* match. This defeats the entire purpose of replay-verify and execute-and-verify: an actual hard-fork-class state divergence (wrong accumulator/state root accepted as valid) goes undetected by the very tool meant to catch it. This satisfies the "Hard-fork-only divergence during commit, replay, restore, or proof verification" criterion in the gate, and can mask corruption of durable ledger data that differs from the correct VM result.

### Likelihood Explanation
The bug is not speculative — it is explicitly acknowledged in the code comment as a real, currently-unmitigated gap, so its existence and root cause are certain from local code alone. Its trigger condition (an execution/storage bug that changes the state root without changing the write-set hash, gas, status, or event root) is a plausible, narrower divergence than a full write-set corruption, and the impact is high because it silently defeats a verification tool that operators rely on to detect divergence before it accumulates. The comment ties the fix specifically to gating `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, indicating this is a known pre-existing gap that has not yet been closed for the pre-existing `state_checkpoint_hash` field either (only mentioned in the context of the new position root, but the state/hot-state checks are also absent from the function body today).

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`'s locally-computed state checkpoint hash (and hot-state / position-state checkpoint hashes when applicable) against `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`, returning an error on mismatch just as it does today for gas, status, write-set hash, and event root hash. Since `TransactionOutput` alone does not carry the computed checkpoint hash, plumb it in as an additional optional parameter (populated by callers that have it, e.g. `chunk_executor::verify_execution` and the CLI/debugger replay paths) so verification is complete before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or any other feature that depends on this comparator being sound.

### Proof of Concept
1. In a replay/verify context (e.g. `db-tool replay-verify` or `aptos-debugger`), take a transaction whose `TransactionInfo` (fetched from the authenticated backup/accumulator proof) has `state_checkpoint_hash = H_correct`.
2. Re-execute the transaction locally such that the resulting write set, gas used, status, and events are byte-identical to the original (trivial if the write set itself is unaffected) but the state-checkpoint construction stage (`DoStateCheckpoint::run`, `do_state_checkpoint.rs`) produces a different SMT root `H_wrong` for the same version (e.g., by injecting a bug/corruption in the SMT update path that only affects the summarized root, not the write set itself).
3. Call `ensure_match_transaction_info(version, txn_info, ..)` as done in `chunk_executor::verify_execution` (`execution/executor/src/chunk_executor/mod.rs:692`) or the CLI path (`aptos-move/cli/src/commands.rs:2811`).
4. Observe that `ensure_match_transaction_info` returns `Ok(())` despite `H_wrong != H_correct`, because the function never inspects `state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` — confirming that a diverged state root passes verification undetected.

Note: I was unable to fully trace all production code paths that gate actual node bootstrapping/consensus (as opposed to backup/replay/CLI tooling) on this comparator within the indexed portion of the repository; the impact analysis above is scoped to the replay-verify and execute-and-verify tooling paths I was able to confirm call this function.

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

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L233-246)
```rust
    fn print_mismatches(
        txn_outputs: &[TransactionOutput],
        expected_txn_infos: &[TransactionInfo],
        first_version: Version,
    ) {
        for idx in 0..txn_outputs.len() {
            let txn_output = &txn_outputs[idx];
            let txn_info = &expected_txn_infos[idx];
            let version = first_version + idx as Version;
            txn_output
                .ensure_match_transaction_info(version, txn_info, None, None)
                .unwrap_or_else(|err| println!("{}", err))
        }
    }
```

**File:** aptos-move/cli/src/commands.rs (L2797-2813)
```rust
        // Materialize into transaction output and check if the outputs match.
        let txn_output = vm_output.into_transaction_output().map_err(|err| {
            CliError::UnexpectedError(format!(
                "Failed to materialize into transaction output: {}",
                err
            ))
        })?;

        // When local package overrides are in use the replayed code diverges from
        // what was originally executed on-chain (different instructions, gas, etc.),
        // so output comparison is meaningless and is automatically skipped.
        let skip_comparison = self.skip_comparison || !self.use_local_package.is_empty();
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```
