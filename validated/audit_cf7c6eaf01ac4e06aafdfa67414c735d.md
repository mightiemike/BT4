## Finding: `TransactionOutput::ensure_match_transaction_info()` never checks state-checkpoint hashes, letting replay-verify tooling accept a corrupted/divergent state root

### Title
Replay/backup-verification accepts wrong `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` as matching — (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the function used by state-sync's chunk-executor replay-verification path and by `aptos-debugger` to confirm that a locally re-executed transaction produces the exact same committed result as an authenticated `TransactionInfo` (the leaf hashed into the transaction accumulator). This function only compares status, gas, the write-set hash (`state_change_hash`), and the event root hash — it explicitly skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, as documented by its own TODO comment. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` builds `ERR_MSG` checks for status, gas, `state_change_hash` (write-set hash) and `event_root_hash`, then returns `Ok(())` without ever reading `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`: [2](#0-1) 

The code's own comment acknowledges the gap:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [3](#0-2) 

This function is the sole correctness gate used by `chunk_executor::verify_execution`, which re-executes backup transactions and checks their outputs against the `TransactionInfo`s already accepted from a backup/archive: [4](#0-3) 

and by `aptos-debugger`'s mismatch-printing/verification utility: [5](#0-4) 

By contrast, the normal state-sync execution path (`StateSyncChunkVerifier::verify_chunk_result`) performs field-complete equality via `ensure_transaction_infos_match`, which compares the whole `TransactionInfo` enum (thus implicitly all checkpoint-hash fields), so live state sync is not affected: [6](#0-5) [7](#0-6) 

The blind spot is isolated to the `verify_execution`/replay-verify/backup-restore-verification tooling path that relies on `ensure_match_transaction_info`.

### Impact Explanation
Backup-restore verification (`db-tool replay-verify`, `replay_on_archive`) and `aptos-debugger` are the tools operators and the Aptos Labs release/QA pipeline use to assert that a restored ledger snapshot, when replayed with the real VM, reproduces the same committed transaction infos as what's stored in the backup/archive (and therefore, transitively, the same accumulator root that a `LedgerInfoWithSignatures` attests to). Because `ensure_match_transaction_info` never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, a divergence confined to the state-tree root (e.g., resulting from a JMT/state-view/native-state-root computation bug, a hard-fork-only bug, or backup data containing a state root that does not correspond to the locally re-derivable write-set) will not be flagged: `state_change_hash` (write set hash) and `event_root_hash` can match while `state_checkpoint_hash` differs, and the check still returns `Ok(())`. This means replay-verify can certify a backup/restore as a "successful replay" while the actual world-state root diverges from the one that the backup falsely claims was produced by consensus — exactly the "hard-fork-only divergence during commit/replay/restore" category this task targets, and it undermines confidence that restore/backup pipelines detect state-tree corruption.

### Likelihood Explanation
The bug is unconditionally present (it is not gated behind the not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` flag; the flag only controls whether *some* callers additionally compute those checkpoint hashes at all). Any divergence limited to checkpoint hashes (as opposed to individual write ops or events) will silently escape detection whenever `ensure_match_transaction_info` is used, which is deterministic and always reproducible — not reliant on adversarial timing or race conditions. It is, however, only reachable via operator/QA-invoked replay-verify and debugging tooling, not via a directly attacker-triggerable consensus/mainnet transaction path, which lowers the acute severity relative to a live consensus divergence but keeps it squarely within the "hard-fork-only divergence during ... replay, restore, or proof verification" bucket in scope.

### Recommendation
Extend `ensure_match_transaction_info` to also compute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` whenever the corresponding value is available/computable from the (optionally passed) state-checkpoint output, matching the TODO's own suggestion, so that `verify_execution` / `replay_on_archive` / `aptos-debugger` cannot certify a backup as correctly replayed when its state-tree root diverges from local execution.

### Proof of Concept
1. Construct (or take) a backup/archive whose stored `TransactionInfo` has a `state_checkpoint_hash` that does not match the JMT root that would actually result from applying the given write set to the given parent state (all other fields — `state_change_hash`, `event_root_hash`, `gas_used`, `status` — left correct/consistent).
2. Run `db-tool`'s replay-verify (which internally invokes `chunk_executor::verify_execution` → `TransactionOutput::ensure_match_transaction_info`) against this backup. [8](#0-7) 
3. Observe that `ensure_match_transaction_info` returns `Ok(())` and the tool reports a successful/matching replay, because the function body at lines 2159–2203 never inspects `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`. [2](#0-1)

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

**File:** execution/executor/src/chunk_executor/chunk_result_verifier.rs (L53-66)
```rust
            let num_overlap = self.txn_infos_with_proof.verify_extends_ledger(
                first_version,
                parent_root_hash,
                Some(first_version),
            )?;
            assert_eq!(num_overlap, 0, "overlapped chunks");

            // Verify transaction infos match
            ledger_update_output
                .ensure_transaction_infos_match(&self.txn_infos_with_proof.transaction_infos)?;

            Ok(())
        })
    }
```
