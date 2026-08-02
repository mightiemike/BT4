### Title
`ensure_match_transaction_info` never checks state/hot-state/position checkpoint hashes, letting replay-verify and single-transaction replay tooling accept a divergent state root as a valid replay - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-replay check used by `db-tool`'s `replay_on_archive`, the chunk executor's execution verification path, `aptos-debugger`, and the `aptos` CLI's transaction-replay command to confirm that a locally re-executed transaction produced the same result as what is committed and accumulator-proven on-chain via `TransactionInfo`. It validates status, gas used, write-set hash (`state_change_hash`) and event root hash, but it explicitly skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the fields that authenticate the actual Merkle root of world state after the transaction. The code even carries a maintainer TODO acknowledging this gap.

### Finding Description
`ensure_match_transaction_info` in `types/src/transaction/mod.rs` is defined as: [1](#0-0) 

It checks `status`, `gas_used`, the write-set hash against `txn_info.state_change_hash()`, and the event root hash — but stops there, with an explicit comment: [2](#0-1) 

`TransactionInfo` carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as separate, independently-computed fields that are accumulator-proven (i.e., part of the value that is hashed into the transaction accumulator and ultimately signed in `LedgerInfo`): [3](#0-2) 

These checkpoint hashes are the sparse-Merkle/Jellyfish-Merkle roots computed over the *entire* world state after applying the write set — not just a hash of the per-transaction write set. They are produced in `DoStateCheckpoint` / `assemble_transaction_infos` during normal execution: [4](#0-3) 

Because `ensure_match_transaction_info` never re-derives or compares these roots, any divergence between a replayed node's computed state tree and the historically committed/authenticated state tree (e.g. from a bug in JMT/SMT construction, state-key hashing, or write-set application logic somewhere in the storage/executor stack) will not be detected by this check even though the write-set bytes and events match bit-for-bit. This function is consumed by several tools whose entire purpose is state-integrity verification:
- `storage/db-tool/src/replay_on_archive.rs` re-executes historical transactions and calls this exact function to confirm the replay is faithful to the archived, accumulator-proven history: [5](#0-4) 
- The chunk executor's `verify_execution` (used for `--verify-execution-mode` style deep replay-verification) calls the same function per transaction: [6](#0-5) 
- `aptos-debugger` and the `aptos` CLI's transaction execute/replay command call the same function for single-transaction diagnostics: [7](#0-6) [8](#0-7) 

By contrast, the ordinary state-sync commit path (`LedgerUpdateOutput::ensure_transaction_infos_match`) does compare the *entire* `TransactionInfo` object, including checkpoint hashes, field-by-field: [9](#0-8) 
That stricter check is only exercised in the state-sync/chunk-executor commit flow (`chunk_result_verifier.rs`), not in the replay-verify/debugger/CLI tools that rely on `ensure_match_transaction_info`.

### Impact Explanation
This breaks the "committed state that differs from correct VM result must be detectable" and "hard-fork-only divergence during commit, replay ... must be caught" invariants explicitly listed as in-scope. Tools whose sole job is to detect execution/state divergence against historical mainnet data (`replay_on_archive`, chunk-executor deep verification, `aptos-debugger`, CLI replay) can report success ("no mismatch") while the authenticated Jellyfish/Sparse Merkle state root has silently diverged from what is actually committed on-chain. This directly undermines the ability to detect a state-corrupting bug (e.g., an incorrect JMT construction, a storage-schema misinterpretation, or non-determinism in state application) using the very tooling built to catch such regressions before or after a mainnet incident. The maintainers' own comment confirms this is a real, currently-active gap tied to the upcoming `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature (native "position" state, i.e., trading-related on-chain state), which introduces a third checkpoint root (`position_state_checkpoint_hash`) that is also unchecked. Given the affected tools are exactly the last line of defense for detecting state-root divergence, the impact rises to High: a genuine state-corruption bug could pass replay-verify undetected.

### Likelihood Explanation
Likelihood is Medium: this is not attacker-triggerable directly (there's no malicious input path — it requires an actual state-computation bug elsewhere to produce a divergent checkpoint hash), but it is a real, currently-shipped gap in code that executes on every replay-verify/debugger run, and the maintainers have already flagged it as a known limitation to be fixed before enabling a related feature. The gap is unconditionally present today, independent of any feature flag.

### Recommendation
In `ensure_match_transaction_info`, when `expected_write_set`/checkpoint data is available, recompute or accept the locally-computed `state_checkpoint_hash` (and, where enabled, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) and assert equality against `txn_info`'s corresponding fields, mirroring what `LedgerUpdateOutput::ensure_transaction_infos_match` already does for the commit path. At minimum, gate `COMPUTE_TRADING_NATIVE_STATE_ROOTS` on this fix as the existing TODO states, and extend the same checkpoint-hash comparison to the non-trading-native `state_checkpoint_hash`/`hot_state_checkpoint_hash` fields as well, since those are unconditionally omitted today.

### Proof of Concept
Conceptual PoC (cannot be executed without a live divergence bug, since this is a missing-check finding, not an injectable exploit):
1. Take any historical transaction range with a known `TransactionInfo` (containing `state_checkpoint_hash`) from an archive.
2. Introduce (or trigger via an unrelated latent bug) a divergence in the locally-computed post-execution state tree that nonetheless produces byte-identical `write_set`, `events`, `gas_used`, and `status` for the replayed transaction (e.g., a bug that corrupts the persisted JMT/SMT but not the transaction's own write-set/output, such as a storage-restore or checkpoint-assembly defect).
3. Run `db-tool replay-on-archive` (`storage/db-tool/src/replay_on_archive.rs`, `execute_and_verify` at lines 388-406) over that range.
4. Observe `ensure_match_transaction_info` returns `Ok(())` — no error is reported — because it never compares `state_checkpoint_hash`, even though the actual state root at that version differs from the archived/authenticated one.

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L88-103)
```rust
                let event_root_hash =
                    InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash();
                let write_set_hash = CryptoHash::hash(txn_output.write_set());
                let status = txn_output
                    .status()
                    .as_kept_status()
                    .expect("Already sorted.");
                let txn_info = if transaction_info_v1 {
                    TransactionInfo::builder_v1()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .maybe_hot_state_checkpoint_hash(
                            hot_state_checkpoint_hashes.and_then(|hot| hot[i]),
                        )
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

**File:** execution/executor/src/chunk_executor/mod.rs (L692-706)
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

**File:** execution/executor-types/src/ledger_update_output.rs (L92-114)
```rust
    pub fn ensure_transaction_infos_match(
        &self,
        transaction_infos: &[TransactionInfo],
    ) -> Result<()> {
        ensure!(
            self.transaction_infos.len() == transaction_infos.len(),
            "Lengths don't match. {} vs {}",
            self.transaction_infos.len(),
            transaction_infos.len(),
        );

        let mut version = self.first_version();
        for (txn_info, expected_txn_info) in
            zip_eq(self.transaction_infos.iter(), transaction_infos.iter())
        {
            ensure!(
                txn_info == expected_txn_info,
                "Transaction infos don't match. version:{version}, txn_info:{txn_info}, expected_txn_info:{expected_txn_info}",
            );
            version += 1;
        }
        Ok(())
    }
```
