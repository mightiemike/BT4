I'll investigate the areas mentioned: write-set conversion, transaction info construction, storage commit, accumulators, Merkle proofs, and restore flows.The `ensure_match_transaction_info` comment explicitly flags a known gap. Let me investigate where this function is used and how significant the checkpoint hash omission is.## Analog Found: `TransactionOutput::ensure_match_transaction_info` Silently Skips Checkpoint-Hash Verification, Blinding `replay_on_archive` to State-Root Divergence

### Title
Replay-verify comparator omits state/hot-state/position checkpoint hash checks, allowing corrupted state roots to pass as verified - (File: types/src/transaction/mod.rs)

### Summary
The external report's bug class is "a value that should gate acceptance/rejection is silently omitted from the check, letting bad state through." The Aptos analog: `TransactionOutput::ensure_match_transaction_info`, the function used by `storage/db-tool/src/replay_on_archive.rs` to authenticate replayed execution against the on-chain-committed `TransactionInfo`, deliberately does not compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the very fields that commit to the state root. A local re-execution whose write set/events/gas/status match but whose state root diverges is reported as a successful, verified replay.

### Finding Description
`ensure_match_transaction_info` checks status, gas, write-set hash, and event-root hash against the given `TransactionInfo`, but explicitly and knowingly stops short of checking the checkpoint hashes: [1](#0-0) 

The comment in the code itself documents the gap: [2](#0-1) 

This comparator is the sole correctness gate used by `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes archived transactions and calls `ensure_match_transaction_info` per transaction to decide pass/fail: [3](#0-2) 

It is also used directly by `aptos-move/cli`'s debugger-replay commands for both user and system transactions: [4](#0-3) [5](#0-4) 

By contrast, the equivalent bulk-list verification path (`TransactionOutputListWithProof::verify`, used in normal state-sync/API verification) also does not check the checkpoint hash against anything either — it only checks events, write set hash, gas, status, and transaction hash — so the state root is never authenticated end-to-end outside of the accumulator hash on the `TransactionInfo` object itself: [6](#0-5) [7](#0-6) 

The `state_checkpoint_hash` is precisely the field that binds a `TransactionInfo` to the SMT/JMT root produced by `DoStateCheckpoint`, and it is one of the components hashed into the `TransactionInfo` itself (which the accumulator commits to): [8](#0-7) 

Because the checkpoint hash is folded into the `TransactionInfo` hash that gets accumulated, this omission does not let an attacker forge a *valid* accumulator/ledger proof — a wrong state-checkpoint hash would still make the recomputed `TransactionInfo` hash differ from the archived (validator-signed) one. However, `ensure_match_transaction_info` never recomputes or compares the `TransactionInfo` hash as a whole; it only checks four sub-fields individually and returns `Ok(())` regardless of whether the state/hot-state/position checkpoint hashes match the archived `TransactionInfo`. This means the *specific signal that a locally-recomputed state root differs from the authenticated on-chain root* is discarded before it can ever surface as a failure.

### Impact Explanation
`replay_on_archive` and the `aptos-db-tool replay-verify` family of tools exist specifically to detect state-computation divergence (e.g., from a Move VM/native bug, storage handoff bug, or hard-fork-only behavior change) before or after it reaches mainnet, by re-executing archived transactions and comparing outputs against the authenticated `TransactionInfo`. Because the comparator silently excludes the checkpoint-hash fields, any bug that corrupts state-root computation while leaving write-set bytes, events, gas, and status unchanged (e.g., an SMT/JMT root computation bug, a hot-state root bug, or the referenced "trading-native" state root feature) will be reported as a clean, verified replay. This directly undermines the integrity guarantee that "replay, restore, and proof verification must not silently reinterpret or accept incorrect committed state" — a bug of this class could reach or persist on mainnet undetected by the very tooling meant to catch it, and archive-based backups/audits relying on this tool would give false assurance of correctness.

### Likelihood Explanation
The gap is unconditional and always present in this code path — it is not behind a feature flag; the code says the checks should be added "before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS," implying the checkpoint-hash comparison is currently entirely absent regardless of that flag's state. Any latent or introduced state-root computation bug (independent root cause) would automatically be masked by this comparator whenever `replay_on_archive`/CLI replay-verification is used as the detection mechanism, making the likelihood of missed detection high whenever such divergence exists.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in the target `TransactionInfo`) against the locally recomputed values, or recompute and compare the full `TransactionInfo::hash()` rather than a subset of fields, so that replay-verify tooling cannot report success when the authenticated state root diverges from local execution.

### Proof of Concept
Not applicable as an executable PoC (this is a verification-logic gap, not an exploitable transaction). Conceptually:
1. Introduce/assume any bug that causes the locally computed state (SMT/JMT) root or hot-state root for a transaction to differ from the archived, validator-signed root, while write set bytes, events, gas, and status remain identical.
2. Run `aptos-db-tool replay-on-archive` (`storage/db-tool/src/replay_on_archive.rs`) or `aptos move replay`/`download` CLI commands over the affected version range.
3. `execute_and_verify` calls `ensure_match_transaction_info`, which passes because it never inspects the checkpoint hash fields, so the tool reports the range as successfully verified despite the state-root divergence.

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

**File:** types/src/transaction/mod.rs (L2941-3022)
```rust
    /// This method will ensure:
    /// 1. All transaction infos exist on the given `ledger_info`.
    /// 2. If `first_transaction_output_version` is None, the transaction output list is empty.
    ///    Otherwise, the list starts at `first_transaction_output_version`.
    /// 3. Events, gas, write set, status in each transaction output match the expected event root hashes,
    ///    the gas used and the transaction execution status in the proof, respectively.
    /// 4. The transaction hashes match those of the transaction infos.
    pub fn verify(
        &self,
        ledger_info: &LedgerInfo,
        first_transaction_output_version: Option<Version>,
    ) -> Result<()> {
        // Verify the first transaction output versions match
        ensure!(
            self.get_first_output_version() == first_transaction_output_version,
            "First transaction and output version ({:?}) doesn't match given version ({:?}).",
            self.get_first_output_version(),
            first_transaction_output_version,
        );

        // Verify the lengths of the transactions and outputs match the transaction infos
        ensure!(
            self.proof.transaction_infos.len() == self.get_num_outputs(),
            "The number of TransactionInfo objects ({}) does not match the number of \
             transactions and outputs ({}).",
            self.proof.transaction_infos.len(),
            self.get_num_outputs(),
        );

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

        // Verify the transaction infos are proven by the ledger info.
        self.proof
            .verify(ledger_info, self.get_first_output_version())?;

        Ok(())
    }
```

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

**File:** storage/aptosdb/src/db/fake_aptosdb.rs (L1147-1180)
```rust
        // Verify the events, status, gas used and transaction hashes.
        itertools::zip_eq(
            &txn_outputs_with_proof.transactions_and_outputs,
            &txn_outputs_with_proof.proof.transaction_infos,
        )
        .map(|((txn, txn_output), txn_info)| {
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
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L95-123)
```rust
                let txn_info = if transaction_info_v1 {
                    TransactionInfo::builder_v1()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .maybe_hot_state_checkpoint_hash(
                            hot_state_checkpoint_hashes.and_then(|hot| hot[i]),
                        )
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .maybe_position_state_checkpoint_hash(
                            position_state_checkpoint_hashes.and_then(|p| p[i]),
                        )
                        .build()
                } else {
                    TransactionInfo::builder_v0()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .build()
                };
                let txn_info_hash = txn_info.hash();
                (txn_info, txn_info_hash)
```
