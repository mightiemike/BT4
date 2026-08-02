### Title
`TransactionOutput::ensure_match_transaction_info` never validates the state-checkpoint (or hot-state / position) root, letting replay-verify tooling accept a divergent state root as a successful replay - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` [1](#0-0)  is the function used by archive-based replay/verification tools (`db-tool`'s `replay_on_archive` and `aptos-debugger`) to confirm that locally re-executed `TransactionOutput`s match the authenticated, consensus-committed `TransactionInfo` pulled from a backup/archive. It checks status, gas used, write-set hash, and event root hash, but it never compares `txn_info.state_checkpoint_hash()` (nor the hot-state or position checkpoint hashes) against anything computed from the replay. The function's own comment openly documents that it "ignores the checkpoint hashes ... so replay-verify tooling ... can report a successful replay even when the authenticated ... state root diverges from local execution." [2](#0-1) 

### Finding Description
`execute_and_verify` in `replay_on_archive.rs` re-executes historical transaction chunks with `AptosVMBlockExecutor::execute_block` (bypassing `DoStateCheckpoint`/`DoLedgerUpdate`, which are the only places that actually compute the Sparse-Merkle state root) and then calls `ensure_match_transaction_info` per-transaction to decide whether the replay matches the archived, ledger-info-proven `TransactionInfo`. [3](#0-2) 

Because `ensure_match_transaction_info` only asserts on `status`, `gas_used`, `write_set` hash, and `event_root_hash`, it structurally cannot detect a mismatch in `state_checkpoint_hash` (the field that commits the actual Jellyfish-Merkle/state root for the version) — that field is simply never read or compared in this function. [4](#0-3)  The equivalent full verification path used for state-sync/consensus (`TransactionOutputListWithProof::verify`) also omits any state_checkpoint_hash comparison against a locally-computed root — it only checks events, write-set hash, gas, status, and transaction hash before trusting the accumulator proof. [5](#0-4) 

The broken invariant: a proof-bearing, ledger-info-signed `TransactionInfo` carries `state_checkpoint_hash` specifically so that a state root computed independently (during replay/verification) can be authenticated against the accumulator-proven value. The replay tool that exists precisely to catch state-divergence bugs (e.g. across binary/protocol upgrades or hard forks) silently skips this check, so it will report "success" even when the resulting Jellyfish Merkle root differs from the one committed on-chain.

### Impact Explanation
This breaks the "committed state must match the correct VM result" and "wrong state proof accepted as valid" invariants required by the state-integrity gate. `replay_on_archive`/`ReplayVerify` are the primary tools operators and Aptos Labs use to certify that a new binary/protocol version reproduces historical state exactly (a critical gate before hard forks and releases). If a change (VM bug, non-determinism, storage-layer bug, or a malicious build) causes the recomputed state root to diverge from the archived, consensus-proven root, this tool will not surface it, because the one field designed to catch exactly that divergence (`state_checkpoint_hash`) is never checked. This is a "hard-fork-only divergence during commit, replay, restore, or proof verification" scenario explicitly called out as in-scope, and it undermines the guarantee that historical replay + `state_checkpoint_hash` verification implies state-root correctness.

### Likelihood Explanation
The gap is deterministic and always present (not conditional on a race or attacker input) — every invocation of `ensure_match_transaction_info` in both `replay_on_archive.rs` and `aptos_debugger.rs` skips the state root comparison. The code's own inline TODO acknowledges the gap exists today and flags it as something that must be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`," but the state_checkpoint_hash omission is not scoped to that feature — it is missing unconditionally for all replay/verification, including the currently-shipped V0/V1 `state_checkpoint_hash` field, independent of the trading-native feature flags.

### Recommendation
Extend `ensure_match_transaction_info` (and the analogous check in `TransactionOutputListWithProof::verify`) to independently recompute the resulting state root from the replayed writes (or otherwise obtain the locally-computed checkpoint hash for that version) and assert it equals `txn_info.state_checkpoint_hash()` (and, when applicable, hot-state / position checkpoint hashes) before treating the replay as verified. Until this is fixed, any tooling or release gate that depends on `replay_on_archive`/`ensure_match_transaction_info` for state-root assurance should not be treated as validating the ledger's Merkle state root.

### Proof of Concept
1. Take an archived (backup) block whose `TransactionInfo.state_checkpoint_hash` is `H` (proven under a ledger info).
2. Introduce (or trigger via a genuine non-determinism/bug) a change in the VM/storage layer that alters the resulting state root to `H' != H` while leaving `write_set` bytes, `gas_used`, `status`, and `events` unchanged (e.g., a purely storage-serialization/aggregation bug that affects the Merkle tree but not the serialized write ops or events).
3. Run `aptos-move/db-tool replay-on-archive` against this block. `execute_and_verify` calls `ensure_match_transaction_info`, which checks only status/gas/write_set hash/event root hash [6](#0-5)  — it never touches `state_checkpoint_hash` — so the tool reports the replay as successful and matching, even though the actual state root committed by consensus differs from the one recomputed locally.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2145)
```rust
    pub fn ensure_match_transaction_info(
        &self,
        version: Version,
        txn_info: &TransactionInfo,
        expected_write_set: Option<&WriteSet>,
        expected_events: Option<&[ContractEvent]>,
    ) -> Result<()> {
```

**File:** types/src/transaction/mod.rs (L2148-2196)
```rust
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

```

**File:** types/src/transaction/mod.rs (L2197-2203)
```rust
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

**File:** storage/db-tool/src/replay_on_archive.rs (L373-405)
```rust
        let executed_outputs = executor
            .execute_block(
                &txns_provider,
                &self
                    .arc_db
                    .state_view_at_version(current_version.checked_sub(1))?,
                BlockExecutorConfigFromOnchain::new_no_block_limit(), // TODO(HotState): will need to incorporate some features.
                TransactionSliceMetadata::Chunk {
                    begin: *current_version,
                    end: *current_version + cur_txns.len() as u64,
                },
            )
            .map(BlockOutput::into_transaction_outputs_forced)?;
        assert_eq!(executed_outputs.len(), cur_txns.len());

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
