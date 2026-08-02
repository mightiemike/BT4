## Finding: Replay/restore verification silently skips state-checkpoint hash checks

### Title
Transaction replay verification (`ensure_match_transaction_info` / `ChunkExecutorInner::verify_execution`) never checks the state-checkpoint hash, letting a corrupted state root pass as a valid replay - (`File: types/src/transaction/mod.rs`, `execution/executor/src/chunk_executor/mod.rs`)

### Summary
The replay-verification path used by `TransactionReplayer` (backup restore / `db-tool replay-verify`) validates a re-executed transaction against the persisted `TransactionInfo` using `TransactionOutput::ensure_match_transaction_info`. That function checks status, gas, write-set hash, and event-root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents with a `TODO(trading-native)` comment.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` in [1](#0-0)  verifies status, gas, write-set hash and event-root hash, but the trailing comment admits: [2](#0-1) 

states that this comparator "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is the *sole* verification performed by `ChunkExecutorInner::verify_execution`, which re-executes a chunk and compares each output to the persisted `TransactionInfo`: [3](#0-2) 

This path is reached only through `enqueue_chunks`/replay, which is used by `TransactionReplayer` — invoked by the backup-restore transaction-replay tooling (`storage/backup/backup-cli/src/backup_types/transaction/restore.rs`) and `db-tool`'s `replay-verify`/`replay_on_archive` subcommands. Unlike the normal execution path (`ChunkExecutorInner::update_ledger`), which routes through `DoStateCheckpoint::run` and explicitly checks the computed root against `known_state_checkpoints`/`known_position_state_checkpoints` ( [4](#0-3) ), the `verify_execution` replay-verification path never calls `DoStateCheckpoint` at all — it only calls `ensure_match_transaction_info`, which skips checkpoint hashes entirely.

### Impact Explanation
This breaks the "restore/replay must preserve deterministic proof binding" invariant: replay-verify tooling that is specifically meant to catch state divergence between an archived, authenticated ledger and independent re-execution can report success even when the underlying Merkle/JMT state root (and, once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, the native "position" state root) is wrong. Since this is the exact tool used to detect non-determinism/consensus bugs or storage corruption across historical mainnet data, a real divergence in committed state (e.g., a VM/state-view bug that alters resource values without affecting the write-set hash comparison paths already covered, or corruption introduced during restore) would go undetected. This matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" impact category — it does not directly corrupt live consensus-committed state, but it defeats the safety net that would otherwise catch such corruption before/after the fact.

### Likelihood Explanation
This is not a remotely triggerable vulnerability by an unprivileged attacker against a live validator — it is a gap in an internal verification/tooling code path (replay-verify, backup restore verification) reachable only when operators run these tools against data that has already diverged from correct state. However, it is a real, self-admitted (via the TODO comment) integrity gap: the tool's entire purpose is to catch exactly this class of divergence, and it structurally cannot, because the only comparison function it relies on omits the checkpoint-hash fields.

### Recommendation
Extend `ensure_match_transaction_info` (or add a companion check invoked by `ChunkExecutorInner::verify_execution`) to also validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against freshly computed values (i.e., route the replay-verify path through `DoStateCheckpoint::run` the same way `update_ledger` does), before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in production, as the comment itself recommends.

### Proof of Concept
Not directly exploitable as a live-network PoC; the issue is demonstrated by code inspection:
1. `execution/executor/src/chunk_executor/mod.rs:692` calls `ensure_match_transaction_info` as the only correctness check in `verify_execution`.
2. `types/src/transaction/mod.rs:2139-2204` shows the function never reads/compares `state_checkpoint_hash()`/`hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()` from `TransactionInfo`.
3. Contrast with `execution/executor/src/workflow/do_state_checkpoint.rs:206-220`, which does perform this comparison but is only invoked by `update_ledger`, not by `verify_execution`.

Given the difficulty of fully tracing every downstream consumer of `verify_execution` (e.g. whether `db-tool replay-verify` CLI wraps additional checks) within available tooling, I was not able to conclusively confirm there is *no* other layer that catches this divergence before it's reported as "success" — the in-code TODO comment is the strongest evidence that this gap is real and currently unmitigated.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L206-220)
```rust
        if let Some(known) = known_state_checkpoints {
            ensure!(
                known.len() == num_txns,
                "Bad number of known {label} hashes. {} vs {}",
                known.len(),
                num_txns,
            );
            if let Some(idx) = last_checkpoint_index {
                ensure!(
                    known[idx] == Some(computed_last_checkpoint_hash),
                    "{label} root hash mismatch with known hashes passed in. {:?} vs {:?}",
                    known[idx],
                    Some(computed_last_checkpoint_hash),
                );
            }
```
