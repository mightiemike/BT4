## Title
`TransactionOutput::ensure_match_transaction_info` skips verifying state/hot-state/position checkpoint roots, letting replay-verify accept a corrupted committed state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) is the authenticity check used by both the chunk executor's `verify_execution` path (`execution/executor/src/chunk_executor/mod.rs:648-709`) and the standalone replay-verification tool `replay_on_archive` (`storage/db-tool/src/replay_on_archive.rs:349-416`, and transitively backup-cli's `VerifyExecutionMode`) to confirm that locally re-executed transactions reproduce the `TransactionInfo` that was already accepted into the accumulator/ledger. The function only checks `status`, `gas_used`, the write-set hash, and the event root hash. It explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the recomputed state, a gap the code itself documents as a known TODO.

### Finding Description
`ensure_match_transaction_info` is the single place that binds a freshly-computed `TransactionOutput` back to an already-committed, accumulator-authenticated `TransactionInfo`: [1](#0-0) 

It checks `status`, `gas_used`, and the write-set hash (via `state_change_hash`) and event root hash, but the function's own trailing comment admits the state-root fields are skipped: [2](#0-1) 

`TransactionInfoV1` carries three separate state-commitment roots that are supposed to be consensus-verified/ledger-committed: `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` [3](#0-2) 
and these are exactly the roots gated behind `HOT_STATE_ROOT_IN_TXN_INFO` and `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, which are described as making these roots "consensus-verified"/"committed to the ledger accumulator": [4](#0-3) 

None of these three roots are compared inside `ensure_match_transaction_info`. This function is called in two integrity-critical verification flows:

1. Chunk-executor `verify_execution` (used to verify a chunk of transactions being replayed against ground-truth `TransactionInfo`s during backup/replay verification): [5](#0-4) 

2. `db-tool`'s standalone `replay_on_archive` binary, whose entire purpose is to detect execution divergence between historical committed data and a fresh re-execution: [6](#0-5) 

Because `write_set_hash` is checked but the state-checkpoint hashes are not, this check can pass even when the locally recomputed Jellyfish Merkle root (main state, hot state, or native-position state) diverges from what was accepted into the ledger's `TransactionInfo`/accumulator. The gap is asymmetric with the normal (non-verify) commit path: there, `DoStateCheckpoint::run()` is fed `known_state_checkpoints`/`known_hot_state_checkpoints`/`known_position_state_checkpoints` and is expected to validate them during real chunk application in `execution/executor/src/chunk_executor/mod.rs:398-413`. But the replay-verify tooling that node operators and auditors rely on to *independently confirm* that historical ledger state is the correct result of VM execution goes through `ensure_match_transaction_info` instead, which silently omits this check.

### Impact Explanation
This breaks the "authenticated API/proof-bearing responses must stay bound to the right ledger version, root, and object" and "committed state that differs from correct VM result" invariants specifically for the *verification* tooling path: if a previously-committed `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` were ever wrong (due to a bug elsewhere, a bad backup, or any other source of state-commitment corruption), `replay_on_archive` and backup-cli's replay-verify mode would report success ("Full replay-verify passed") even though the ledger's authenticated state root does not match the correct VM result. This defeats the primary safety mechanism relied upon to catch exactly the kind of state-corrupting divergence this task is scoped to find, and it does so silently and without any error surfaced to the operator, undermining confidence in state-root integrity across restore/replay flows.

### Likelihood Explanation
The gap is unconditionally present in the shared verification helper for anyone running `db-tool replay-on-archive` or backup-cli's verify/replay-verify commands — no special privileges or attacker action are needed to trigger the missing check; it simply never fires. The severity is contingent on some other source of state-root divergence existing (this bug does not itself create wrong state, it removes a checkpoint on wrong state), so likelihood of the omission mattering depends on a first-order state-corruption bug slipping through, but as a proof/verification integrity defect in its own right it is deterministic and always reproducible.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`'s recomputed state/hot-state/position checkpoint hashes (when available/enabled) against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()`, returning an error on mismatch just as is done for the write-set and event-root hashes. Since `TransactionOutput` alone doesn't carry the computed checkpoint roots, thread through the checkpoint-stage state summary (as already computed via `DoStateCheckpoint`) to the verification call sites in `execution/executor/src/chunk_executor/mod.rs` and `storage/db-tool/src/replay_on_archive.rs`, and validate it there before declaring a chunk verified.

### Proof of Concept
Not applicable as an exploit script — the flaw is a missing assertion in existing verification code, provable purely by reading `ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`): tracing every field compared shows `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` are never read from `txn_info`, while the function's own comment (lines 2197-2202) explicitly confirms this causes replay-verify tooling to report success despite an authenticated state-root divergence.

**Note on completeness:** I was unable to fully inspect `storage/backup/backup-cli/src/backup_types/transaction/restore.rs`'s `verify_execution`/`VerifyExecutionMode` call sites beyond the imports (tool budget exhausted before reading the function bodies), so I cannot cite the exact line numbers confirming backup-cli's replay-verify command routes through this same function, though the `VerifyExecutionMode` type and `ChunkExecutorTrait`/`TransactionReplayer` imports strongly suggest it shares the `chunk_executor::verify_execution` path already cited above.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2178)
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
```

**File:** types/src/transaction/mod.rs (L2196-2204)
```rust

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

**File:** types/src/on_chain_config/aptos_features.rs (L203-209)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
    /// When enabled, execution populates `TransactionInfoV1`'s hot state root hash, so it
    /// is committed to the ledger accumulator. Requires `TRANSACTION_INFO_V1`.
    HOT_STATE_ROOT_IN_TXN_INFO = 123,
```

**File:** execution/executor/src/chunk_executor/mod.rs (L684-706)
```rust
        // not `zip_eq`, deliberately
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
