## Finding

### Title
Replay/Chunk-Execution Verification Silently Ignores State-Checkpoint Hash Mismatches — (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info`, the single verification routine used by both the chunk executor's replay verification (`execute_and_verify` / `verify_execution`) and the `db-tool replay-on-archive` tool, checks execution status, gas used, write-set hash, and event-root hash against the expected `TransactionInfo` — but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This is not a hidden bug; it is explicitly flagged by an unresolved `TODO` in the function itself.

### Finding Description
`ensure_match_transaction_info` is defined at [1](#0-0) . It validates:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set_hash` vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

but the function ends with an explicit TODO instead of validating checkpoint hashes: [2](#0-1) 

This comparator is invoked from two production integrity-checking call sites:
1. `ChunkExecutor::verify_execution`, which re-executes a chunk and compares each transaction output against transaction infos supplied from a chunk/backup source: [3](#0-2) 
2. The `db-tool replay-on-archive` verifier's `execute_and_verify`, which re-executes archived transactions and compares outputs to the `expected_txn_infos` read from backup: [4](#0-3) 

Because `state_checkpoint_hash` (and the newer `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` fields introduced for `TransactionInfoV1`, defined at [5](#0-4) ) are never compared, any divergence between the locally-computed state/position Merkle root and the authenticated root carried in the archived/chunk `TransactionInfo` is not detected by this path. The state checkpoint hash is exactly the field that binds a transaction to the resulting global state Merkle root (assembled in `assemble_transaction_infos`, which does populate `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` when building the real ledger: [6](#0-5) ). So while the *original* commit path populates and hashes these fields into the accumulator leaf, the *verification/replay* comparator used for chunk sync verification and archive replay-verify tooling has a hole that ignores them.

### Impact Explanation
This breaks the state-commitment/proof-integrity invariant that "committed state that differs from the correct VM result... must be caught." A chunk executor performing `verify_execution` (used to validate synced/chunk-replayed data against an archive or fast-sync source) or the `replay-on-archive` tool can report a chunk/transaction as successfully verified even though the resulting state (or position-tree) root computed by local re-execution diverges from the authenticated root recorded in the transaction info being verified. Since accumulator leaves are hashed from the full `TransactionInfo` (including checkpoint hashes), a divergence here means the accumulator/ledger root that the verified chunk implicitly represents does not actually match what local execution produced — yet the tool reports success. This is a proof-integrity gap: a wrong state root can be accepted as verified, undermining confidence in replay-verify and any downstream policy that trusts a "passed" verification (e.g., promoting an archived/synced range as trusted, or auditing historical state divergence introduced by upgrades/hard forks).

### Likelihood Explanation
The gap is on an always-executed hot path for two operator/verification tools (`verify_execution` in the chunk executor and `replay-on-archive`), not behind an obscure feature flag — every invocation of these checks skips checkpoint-hash validation. Triggering the actual state divergence requires an underlying execution/state-computation discrepancy (e.g., from a future feature flip such as `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, or any bug/hard-fork behavior affecting state-tree assembly), but once such a discrepancy exists, this verification code path is guaranteed to fail to catch it, since the check is structurally absent rather than conditionally bypassed.

### Recommendation
Extend `ensure_match_transaction_info` to also assert that the locally computed state checkpoint hash (and, where applicable, hot-state and position-state checkpoint hashes) matches the corresponding fields on `txn_info`, mirroring how `assemble_transaction_infos` populates them during normal commit. At minimum, gate `COMPUTE_TRADING_NATIVE_STATE_ROOTS`-dependent replay tooling on this validation before treating a chunk/replay as verified.

### Proof of Concept
1. Feed `execute_and_verify` (in `storage/db-tool/src/replay_on_archive.rs`) or `ChunkExecutor::verify_execution` an `expected_txn_info` whose `state_checkpoint_hash` is deliberately wrong (e.g., swapped for a hash from a different version) while keeping `write_set`, `events`, `gas_used`, and `status` correct/matching.
2. Observe that `ensure_match_transaction_info` returns `Ok(())` because it never inspects `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`.
3. The replay/verification tool reports success despite the state root divergence, analogous to the external report's missing-check class (missing validation before accepting a result as authoritative).

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L82-121)
```rust
                let state_checkpoint_hash = state_checkpoint_hashes[i];
                let event_hashes = txn_output
                    .events()
                    .iter()
                    .map(CryptoHash::hash)
                    .collect::<Vec<_>>();
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
```
