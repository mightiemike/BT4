### Title
`ensure_match_transaction_info` never validates `state_checkpoint_hash`, allowing a crafted `TransactionInfo` with a wrong state root to pass chunk-executor verification - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` — the function used by `ChunkExecutorInner::verify_execution` (`execution/executor/src/chunk_executor/mod.rs`) to check that VM-recomputed transaction outputs match an untrusted, incoming `TransactionInfo` — checks status, gas, write-set hash and event-root hash, but explicitly skips comparing `state_checkpoint_hash` (and `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`). This is even called out in a `TODO` comment in the function itself.

### Finding Description
`ensure_match_transaction_info` performs four `ensure!` checks: transaction status, gas used, write-set hash vs `txn_info.state_change_hash()`, and event-root hash vs `txn_info.event_root_hash()`. [1](#0-0) 

It never compares the locally VM-computed state root/state-checkpoint hash against `txn_info.state_checkpoint_hash()` (or the hot-state/position-state checkpoint hash variants). The comment right before returning `Ok(())` explicitly acknowledges this gap: [2](#0-1) 

This function is invoked in `ChunkExecutorInner::verify_execution`, which is the replay/verify path (`aptos-debugger`, replay-verify tooling, `db-tool`'s `replay_on_archive`) that re-executes untrusted/state-sync-provided transactions and checks the recomputed `TransactionOutput` against an externally supplied `TransactionInfo` before treating the chunk as validated: [3](#0-2) 

Because `state_checkpoint_hash` is not part of the comparison, a `TransactionInfo` whose `state_checkpoint_hash` diverges from the actual computed state root — while `state_change_hash` (write set hash), `event_root_hash`, `gas_used`, and `status` all match the executed output — will still pass this check. This is distinct from the accumulator-inclusion verification (`ledger_update_output.ensure_transaction_infos_match`, `TransactionInfoListWithProof::verify_extends_ledger`) used for state-sync chunk acceptance, which binds the accumulator to the supplied `TransactionInfo` list without independently re-deriving `state_checkpoint_hash` from local execution either — the same gap is present in `TransactionOutputListWithProof::verify`, whose per-field checks also omit `state_checkpoint_hash`: [4](#0-3) 

### Impact Explanation
This matters primarily for offline/verification tooling (replay-verify, `db-tool replay_on_archive`, `aptos-debugger`) whose entire purpose is to detect divergence between locally-recomputed state and an authenticated `TransactionInfo`/accumulator. Because `state_checkpoint_hash` is never checked, these tools can report a "successful" replay/verify even though the state root they computed locally differs from what's bound into the (proof-verified) `TransactionInfo`. This undermines the state-integrity guarantee that verify tooling is supposed to provide — a state root divergence (e.g. from a subtle non-determinism bug, storage schema issue, or a maliciously/erroneously constructed segment of transaction info/write-set data) would not be caught by this specific check.

### Likelihood Explanation
This is not a hypothetical crafted-attacker path in the sense of bypassing consensus itself (validators independently execute and vote on the real state root, so this doesn't let a bad state root get committed by honest validators). Its main exposure is in state-sync/chunk-executor and replay-verify code paths that trust an already-proof-verified `TransactionInfo` list and use `verify_execution`/`ensure_match_transaction_info` as a secondary local-execution sanity check. The gap is deterministic and 100% reproducible whenever the state-checkpoint-hash components diverge while other fields coincide — the code comment itself flags this as a known, intentional near-term limitation ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS").

### Recommendation
Add explicit comparisons of `txn_info.state_checkpoint_hash()` (and, where applicable, `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`) against the locally computed state-checkpoint hash inside `ensure_match_transaction_info`, and add the equivalent check to `TransactionOutputListWithProof::verify`, before treating a chunk/transaction info as verified.

### Proof of Concept
A unit test constructing a `TransactionOutput` and a `TransactionInfo` where `status`, `gas_used`, write-set hash, and event-root hash all match, but `state_checkpoint_hash` is set to an arbitrary/incorrect `HashValue`, would call `ensure_match_transaction_info` and observe it return `Ok(())` instead of an error — confirming the state-checkpoint hash is not verified.

### Citations

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

**File:** types/src/transaction/mod.rs (L2970-3015)
```rust
        // Verify the events, write set, status, gas used and transaction hashes.
        self.transactions_and_outputs.par_iter().zip_eq(self.proof.transaction_infos.par_iter())
        .map(|((txn, txn_output), txn_info)| {
            // Check the events against the expected events root hash
            verify_events_against_root_hash(&txn_output.events, txn_info)?;

            // Verify the write set matches for both the transaction info and output
            let write_set_hash = CryptoHash::hash(&txn_output.write_set);
            ensure!(
                txn_info.state_change_hash() == write_set_hash,
                "The write set in transaction output does not match the transaction info \
                     in proof. Hash of write set in transaction output: {}. Write set hash in txn_info: {}.",
                write_set_hash,
                txn_info.state_change_hash(),
            );

            // Verify the gas matches for both the transaction info and output
            ensure!(
                txn_output.gas_used() == txn_info.gas_used(),
                "The gas used in transaction output does not match the transaction info \
                     in proof. Gas used in transaction output: {}. Gas used in txn_info: {}.",
                txn_output.gas_used(),
                txn_info.gas_used(),
            );

            // Verify the execution status matches for both the transaction info and output.
            ensure!(
                *txn_output.status() == TransactionStatus::Keep(txn_info.status().clone()),
                "The execution status of transaction output does not match the transaction \
                     info in proof. Status in transaction output: {:?}. Status in txn_info: {:?}.",
                txn_output.status(),
                txn_info.status(),
            );

            // Verify the transaction hashes match those of the transaction infos
            let txn_hash = txn.committed_hash();
            ensure!(
                txn_hash == txn_info.transaction_hash(),
                "The transaction hash does not match the hash in transaction info. \
                     Transaction hash: {:x}. Transaction hash in txn_info: {:x}.",
                txn_hash,
                txn_info.transaction_hash(),
            );
            Ok(())
        })
        .collect::<Result<Vec<_>>>()?;
```

**File:** execution/executor/src/chunk_executor/mod.rs (L685-708)
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
        Ok(end_version)
    }
```
