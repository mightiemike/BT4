## State-Integrity Analog: `verify_execution` accepts a divergent state/hot-state root because `ensure_match_transaction_info` skips checkpoint-hash comparison

### Title
`TransactionOutput::ensure_match_transaction_info` never validates `state_checkpoint_hash` (state/hot-state root), letting chunk replay-verification pass on a divergent state tree - ([File: types/src/transaction/mod.rs])

### Summary
`ensure_match_transaction_info` is the routine that `chunk_executor::verify_execution` uses to decide whether locally re-executed output matches the authenticated `TransactionInfo` carried by a downloaded/backed-up chunk. It checks status, gas, write-set hash, and event-root hash, but explicitly does **not** check `state_checkpoint_hash` (nor hot-state/position checkpoint hashes), as acknowledged by the `TODO(trading-native)` comment directly above the `Ok(())` return.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` [1](#0-0)  is called from `ChunkExecutorInner::verify_execution` [2](#0-1)  after independently re-executing a chunk of transactions and comparing the freshly computed `TransactionOutput`s against the `TransactionInfo`s that arrived (and were already accumulator-proof-verified) with the chunk.

The comparator validates:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set_hash` (`CryptoHash::hash(self.write_set())`) vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

It deliberately skips comparing the locally-computed state (and hot-state / position) checkpoint hash against `txn_info.state_checkpoint_hash()` [3](#0-2) . The comment states this is a known gap left open specifically so that "replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

Because `write_set_hash` only authenticates the *write set of the single transaction* (the values written), it says nothing about the *cumulative Sparse-Merkle-Tree/Jellyfish-Merkle root* produced by applying that write set on top of the previous state. The `state_checkpoint_hash` field of `TransactionInfo` is the only committed, accumulator-authenticated value that binds a version to the actual global state root [4](#0-3) . Skipping it means `verify_execution` can declare a chunk "verified" even though the locally rebuilt state tree (and therefore all subsequent proofs served from it) has a different root than what the network/backup archive attests to.

### Impact Explanation
`verify_execution`/replay-verify is one of the few code paths whose entire purpose is to catch a state-root divergence between local execution and the authenticated ledger (used for restore/replay-verify workflows and for db-tool's `replay_on_archive`, as directly named in the TODO). By omitting the `state_checkpoint_hash` check, a bug in VM execution, state-merge logic, or hot-state promotion that corrupts the resulting state root — while still coincidentally reproducing correct per-transaction write sets, events, gas and status — would go completely undetected by this verification gate. This is exactly the class of "hard-fork-only divergence during replay/restore" that the state-integrity gate calls out: committed/replayed state can silently differ from the authenticated root without being flagged, defeating the purpose of the replay-verify safety net that operators rely on before trusting a restored/replayed archive.

### Likelihood Explanation
This is not a hypothetical attacker-triggered exploit; it's a structural verification gap present in the shipped comparator itself, guarded by a TODO that says the gap must be closed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`." Any state-root-affecting execution bug (e.g., in hot-state merge, delayed-field materialization order, or resource-group serialization changes covered by other code in this same audit) would silently pass `verify_execution` today. Likelihood of the underlying state-corruption event is uncertain (depends on other bugs), but the detection mechanism itself is provably broken by inspection of this function, independent of any other bug.

### Recommendation
Add a comparison in `ensure_match_transaction_info` between the locally-computed state root (and, where applicable, hot-state and position-state checkpoint roots) and `txn_info.state_checkpoint_hash()` / hot-state equivalents whenever those hashes are present on `TransactionInfo`, rather than deferring this to a future feature flag. At minimum, `chunk_executor::verify_execution` should independently recompute the state Merkle root for the verified range and assert equality with the persisted `state_checkpoint_hash` before considering the batch verified.

### Proof of Concept
Not directly exploitable by an external, unprivileged actor without an additional state-corrupting bug; the finding is a proof-by-code-inspection that the verification routine's contract is violated:
1. `ChunkExecutorInner::verify_execution` re-executes `[begin_version, end_version)` and calls `txn_out.ensure_match_transaction_info(version, txn_info, Some(write_set), Some(events))` [5](#0-4) .
2. `ensure_match_transaction_info` compares status/gas/write-set-hash/event-root-hash only, then returns `Ok(())` without ever reading `txn_info.state_checkpoint_hash()` [6](#0-5) .
3. Construct (or trigger via any state-affecting bug) a `TransactionOutput` whose write set/events/gas/status match the persisted `TransactionInfo` bit-for-bit, but whose application to the prior state produces a different Jellyfish Merkle root than `txn_info.state_checkpoint_hash()` — `verify_execution` returns `Ok(end_version)`, i.e. "verified," despite the state-root mismatch.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L648-708)
```rust
    fn verify_execution(
        &self,
        transactions: &[Transaction],
        persisted_aux_info: &[PersistedAuxiliaryInfo],
        transaction_infos: &[TransactionInfo],
        write_sets: &[WriteSet],
        event_vecs: &[Vec<ContractEvent>],
        begin_version: Version,
        end_version: Version,
        verify_execution_mode: &VerifyExecutionMode,
    ) -> Result<Version> {
        // Execute transactions.
        let parent_state = self.commit_queue.lock().latest_state().clone();
        let state_view = self.state_view(parent_state.latest())?;
        let txns = transactions
            .iter()
            .take((end_version - begin_version) as usize)
            .cloned()
            .map(|t| t.into())
            .collect::<Vec<SignatureVerifiedTransaction>>();

        let auxiliary_info = persisted_aux_info
            .iter()
            .take((end_version - begin_version) as usize)
            .map(|persisted_aux_info| AuxiliaryInfo::new(*persisted_aux_info, None))
            .collect::<Vec<_>>();
        let onchain_config = chunk_onchain_config(&state_view)?;
        let execution_output = DoGetExecutionOutput::by_transaction_execution::<V>(
            &V::new(),
            txns.into(),
            auxiliary_info,
            &parent_state,
            state_view,
            onchain_config,
            TransactionSliceMetadata::chunk(begin_version, end_version),
        )?;
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
        Ok(end_version)
    }
```

**File:** storage/aptosdb/src/db/test_helper.rs (L169-187)
```rust
                // calculate state checkpoint hash and this must be the last txn
                let state_checkpoint_hash = if txn.has_state_checkpoint_hash() {
                    Some(state_checkpoint_root_hash)
                } else {
                    None
                };

                let auxiliary_info = AuxiliaryInfo::new(PersistedAuxiliaryInfo::V1 { transaction_index: idx as u32 }, None);

                let txn_info = TransactionInfo::builder_v0()
                    .transaction_hash(txn.transaction().committed_hash())
                    .state_change_hash(txn.write_set().hash())
                    .event_root_hash(event_root_hash)
                    .maybe_state_checkpoint_hash(state_checkpoint_hash)
                    .gas_used(placeholder_txn_info.gas_used())
                    .status(placeholder_txn_info.status().clone())
                    .maybe_auxiliary_info_hash(auxiliary_info.persisted_info_hash())
                    .build();
                txn_accumulator = txn_accumulator.append(&[txn_info.hash()]);
```
