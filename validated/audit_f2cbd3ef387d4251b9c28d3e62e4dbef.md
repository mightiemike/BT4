### Title
`ensure_match_transaction_info` skips state/hot-state/position checkpoint hash validation, letting replay-verify accept a divergent committed state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative comparator used to confirm that a locally re-executed `TransactionOutput` matches the trusted, accumulator-signed `TransactionInfo` for a version. It is invoked from the chunk executor's `verify_execution` path (used during transaction replay / fast-sync / `replay-verify` tooling) and from CLI/db-tool replay helpers. The comparator checks status, gas, write-set hash, and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents as a known TODO.

### Finding Description
`ensure_match_transaction_info` computes and compares only the write-set hash (`state_change_hash`) and event root hash against the `TransactionInfo`, but the function's own comment states it "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`)": [1](#0-0) 

Because the write set is compared as a hash of the raw `WriteSet` (the pre-materialized key/value updates), and the JMT/hot-state/position roots are separate, derived commitments computed by applying that write set to a versioned state tree, matching the write-set hash alone does **not** guarantee the derived state root actually matches. If there is any divergence between the write set and the actual persisted state root (e.g., a bug in JMT construction, hot-state merge, or the newer native "position" Merkle structure referenced by `position_state_checkpoint_hash`), this comparator will still report success.

This comparator is not confined to a debug-only tool — it backs the chunk executor's `verify_execution`, which is called from `remove_and_replay_epoch`/`TransactionReplayer`, itself part of the executor crate used in state-sync/backup replay-verify flows: [2](#0-1) [3](#0-2) 

The same comparator is also the one used by `db-tool`'s `replay_verify` / backup-cli's replay-verify coordinators to attest that an archived node's replayed history matches on-chain execution.

### Impact Explanation
This breaks the "proof/commitment must survive executor→storage handoff unchanged" invariant for the state-checkpoint (and hot-state / position) roots specifically. `replay-verify` and replay-based sync/audit tooling is the trust mechanism operators and auditors rely on to assert that a node's locally computed ledger state matches the authenticated, validator-signed history. If a bug elsewhere (JMT construction, hot-state merge logic, or the position-tree feature gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) causes the locally computed state root to diverge from the true committed root while write sets/events/status/gas still match, this comparator will silently pass, masking real state corruption instead of failing loudly. This is exactly the class of "authenticated state/proof output accepted despite being wrong" impact called out by the state-integrity gate.

### Likelihood Explanation
The likelihood of the underlying state-root divergence occurring depends on separate bugs in state-tree construction (not evidenced here), but the comparator gap itself is unconditional and always present for any code path invoking `ensure_match_transaction_info` — it is not decided by attacker input, it is a structural blind spot in the verifier. The comment in the code confirms the developers are aware this masks divergence specifically for the position/native state root once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, but the gap for the base `state_checkpoint_hash`/`hot_state_checkpoint_hash` fields already exists today regardless of that flag.

### Recommendation
Extend `ensure_match_transaction_info` to recompute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present) against values independently derived from applying the transaction's write set to the relevant state trees, rather than relying solely on the write-set hash and event root hash. Until this is done, `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and any code path that treats a "successful" `ensure_match_transaction_info` result as proof of full ledger-state equivalence should not be trusted for security-relevant replay verification.

### Proof of Concept
Not directly exploitable as a standalone PoC without a companion bug that produces a wrong state/hot-state/position root while preserving an identical write-set hash; the finding is that the verifier's own documented scope explicitly excludes checking these roots, so such a divergence — however it arises — would go undetected by `ensure_match_transaction_info`, as shown by the comparator implementation itself: [4](#0-3)

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

**File:** execution/executor/src/chunk_executor/mod.rs (L574-646)
```rust
    fn remove_and_replay_epoch(
        &self,
        transactions: &mut Vec<Transaction>,
        persisted_aux_info: &mut Vec<PersistedAuxiliaryInfo>,
        transaction_infos: &mut Vec<TransactionInfo>,
        write_sets: &mut Vec<WriteSet>,
        event_vecs: &mut Vec<Vec<ContractEvent>>,
        begin_version: Version,
        end_version: Version,
        verify_execution_mode: &VerifyExecutionMode,
    ) -> Result<usize> {
        // we try to apply the txns in sub-batches split by known txns to skip and the end of the batch
        let txns_to_skip = verify_execution_mode.txns_to_skip();
        let mut batch_ends = txns_to_skip
            .range(begin_version..end_version)
            .chain(once(&end_version));

        let mut chunks_enqueued = 0;

        let mut batch_begin = begin_version;
        let mut batch_end = *batch_ends.next().unwrap();
        while batch_begin < end_version {
            if batch_begin == batch_end {
                // batch_end is a known broken version that won't pass execution verification
                self.remove_and_apply(
                    transactions,
                    persisted_aux_info,
                    transaction_infos,
                    write_sets,
                    event_vecs,
                    batch_begin,
                    batch_begin + 1,
                )?;
                chunks_enqueued += 1;
                info!(
                    version_skipped = batch_begin,
                    "Skipped known broken transaction, applied transaction output directly."
                );
                batch_begin += 1;
                batch_end = *batch_ends.next().unwrap();
                continue;
            }

            // Try to run the transactions with the VM
            let next_begin = if verify_execution_mode.should_verify() {
                self.verify_execution(
                    transactions,
                    persisted_aux_info,
                    transaction_infos,
                    write_sets,
                    event_vecs,
                    batch_begin,
                    batch_end,
                    verify_execution_mode,
                )?
            } else {
                batch_end
            };
            self.remove_and_apply(
                transactions,
                persisted_aux_info,
                transaction_infos,
                write_sets,
                event_vecs,
                batch_begin,
                next_begin,
            )?;
            chunks_enqueued += 1;
            batch_begin = next_begin;
        }

        Ok(chunks_enqueued)
    }
```

**File:** execution/executor/src/chunk_executor/mod.rs (L648-707)
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
```
