## Title
`ensure_match_transaction_info` never verifies the state/hot-state/position checkpoint hash, letting replay-verify tooling accept a corrupted state root - ([File: types/src/transaction/mod.rs])

### Summary
The single-transaction integrity check used by Aptos's replay-verification tooling, `TransactionOutput::ensure_match_transaction_info`, compares transaction status, gas used, write-set hash, and event root hash between a freshly re-executed `TransactionOutput` and a backup-supplied `TransactionInfo` — but it never compares the `state_checkpoint_hash` (nor `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) field of the `TransactionInfo`.

### Finding Description
`ensure_match_transaction_info` is defined at: [1](#0-0) 

It explicitly checks status, gas, write-set hash, and event root hash, but the function's own trailing comment documents the gap: [2](#0-1) 
"this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is the sole per-transaction verification primitive used by `storage/db-tool/src/replay_on_archive.rs`, which is the tool operators run to independently re-execute an archived transaction range and confirm it matches what is claimed in the backup/archive `TransactionInfo` list, without trusting the archive's accumulator proof at face value: [3](#0-2) 

Because `state_checkpoint_hash` (the Jellyfish Merkle / sparse-Merkle state root recorded at a checkpoint boundary) is never independently recomputed and compared here, a `TransactionInfo` entry in the backup/archive stream whose `state_checkpoint_hash` does not correspond to the actual resulting state (e.g., corrupted during storage/transport, or maliciously substituted by whoever produced the backup) will pass `ensure_match_transaction_info` even though the write set was applied correctly and the txn hash/gas/events all matched. The `write_set` (state_change_hash) is checked, but the state_checkpoint_hash — the actual committed state root snapshot that downstream restore/light-client logic trusts as authoritative for state-proof verification — is not.

This matters because `state_checkpoint_hash` is precisely the value that other parts of the system treat as authoritative to verify state proofs against (e.g. `state_proof.verify(txn_info.state_checkpoint_hash()...)` in the genesis test, and `DbStateView`'s `maybe_verify_against_state_root_hash` derived from `transaction_info.state_checkpoint_hash()`): [4](#0-3) [5](#0-4) 

### Impact Explanation
`replay_on_archive` is the designated independent-verification path for backup archive integrity ("replay and verify"). Its entire purpose is to catch state corruption or malicious tampering in archived data by comparing VM re-execution results against the archive's claimed `TransactionInfo`. Since the state_checkpoint_hash field — which downstream state-proof verification and node bootstrapping/restore trust as the state root — is excluded from comparison, a corrupted or malicious archive that has the correct write set/events/status but a wrong `state_checkpoint_hash` will pass verification silently. Any node or auditor relying on this tool to validate archived history before importing/trusting it would falsely conclude the state root is correct, when in fact it is wrong. This is a proof-integrity gap: the "authenticated" state root binding is not actually checked by the one tool designed to authenticate it independently of the signed accumulator proof chain.

### Likelihood Explanation
This is a straightforward, always-reachable code path (not a race condition or privileged-actor-only scenario): every invocation of `db-tool replay-verify` on any archive/version range calls `ensure_match_transaction_info`, and the check unconditionally skips the checkpoint-hash comparison for all transactions, including checkpoint transactions. Because the code comment itself acknowledges the gap ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), this is a confirmed, self-documented incompleteness rather than a speculative issue.

### Recommendation
In `ensure_match_transaction_info` (`types/src/transaction/mod.rs`), after computing/using `state_summary`/checkpoint information available to the caller, compare `txn_info.state_checkpoint_hash()` (and, where applicable, `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`) against the checkpoint hash computed from re-execution for checkpoint-boundary transactions, and fail verification on mismatch, mirroring the existing `write_set_hash`/`event_root_hash` checks. This should be wired through in `replay_on_archive.rs`'s `execute_and_verify`, which currently doesn't supply or check any computed state-checkpoint hash at all.

### Proof of Concept
1. Take any archived transaction range containing a checkpoint transaction (state_checkpoint_hash is `Some(...)`).
2. Tamper with the archive so the `TransactionInfo.state_checkpoint_hash` for that transaction is replaced with an arbitrary/incorrect `HashValue`, while leaving the transaction, write set, events, gas, and status untouched.
3. Run `db-tool replay-on-archive` (or the equivalent code path in `execute_and_verify`, `storage/db-tool/src/replay_on_archive.rs:392-397`) over that range.
4. `ensure_match_transaction_info` is called with `expected_txn_infos[idx]` carrying the tampered hash; since the function never reads/compares `state_checkpoint_hash`, `write_set_hash`, `gas_used`, `status`, and `transaction_hash` all still match, so `Ok(())` is returned and the tampered archive is reported as verified with no error, despite carrying an incorrect state root claim. [1](#0-0) [3](#0-2)

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

**File:** storage/storage-interface/src/state_store/state_view/db_state_view.rs (L126-141)
```rust
        if let Some(version) = version {
            let txn_with_proof =
                db.get_transaction_by_version(version, ledger_info.version(), false)?;
            txn_with_proof.verify(ledger_info)?;

            let state_root_hash = txn_with_proof
                .proof
                .transaction_info
                .state_checkpoint_hash()
                .ok_or_else(|| StateViewError::NotFound("state_checkpoint_hash".to_string()))?;

            Ok(DbStateView {
                db,
                version: Some(version),
                maybe_verify_against_state_root_hash: Some(state_root_hash),
            })
```

**File:** execution/executor/tests/storage_integration_test.rs (L58-64)
```rust
    state_proof
        .verify(
            txn_info.state_checkpoint_hash().unwrap(),
            account_resource_path.hash(),
            aptos_framework_account_resource.as_ref(),
        )
        .unwrap();
```
