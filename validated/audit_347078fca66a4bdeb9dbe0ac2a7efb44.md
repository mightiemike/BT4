### Title
`replay_on_archive`/replay-verify tooling silently accepts wrong state-checkpoint (SMT) roots — `ensure_match_transaction_info` never validates `state_checkpoint_hash` - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by Aptos's replay-verify infrastructure (`storage/db-tool/src/replay_on_archive.rs`) to check that locally re-executed transaction outputs match the `TransactionInfo` recorded in an authenticated backup/archive. The function validates status, gas, write-set hash (`state_change_hash`), and event root hash, but explicitly and admittedly skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the fields that authenticate the actual world-state (Sparse/Jellyfish Merkle) root.

### Finding Description
`ensure_match_transaction_info` ( [1](#0-0) ) checks:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `CryptoHash::hash(write_set)` vs `txn_info.state_change_hash()`
- computed event root vs `txn_info.event_root_hash()`

but then has an explicit TODO acknowledging the gap:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [2](#0-1) 

This is called directly by `Verifier::execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs`, which drives mainnet-backup replay verification: it executes the block via `AptosVMBlockExecutor`, then calls `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_infos[idx], ...)` and treats an `Ok(())` result as a passing verification for that transaction [3](#0-2) . Because `TransactionOutput` itself carries no `state_checkpoint_hash`/SMT root field (only `write_set`, `events`, `gas_used`, `status`, `auxiliary_data`) [4](#0-3) , and the comparator never attempts to reconstruct/compare a state root, the tool never notices if the write set is byte-for-byte identical to the archive's write set but the *authenticated* checkpoint root recorded in the archived `TransactionInfo` was computed differently (e.g., via a different position-state/hot-state root derivation, a JMT/commit bug, or archive tampering of the `position_state_checkpoint_hash`/`state_checkpoint_hash` fields alone).

Contrast this with the DB's own internal test helpers, which do treat `state_checkpoint_hash` as security-critical and verify state values against it with a Merkle proof (`verify_snapshots` in `storage/aptosdb/src/db/test_helper.rs`) [5](#0-4) , and state-sync's bootstrapper, which strictly checks chunk root hashes against `ensure_state_checkpoint_hash()`/`position_state_checkpoint_hash()` before accepting synced state [6](#0-5) . The replay-verify path is the odd one out: it is the tool specifically responsible for independently confirming that Aptos's committed ledger state matches VM re-execution, yet it omits the one check (state root) that actually proves state-commitment correctness end-to-end.

### Impact Explanation
This is a proof/commitment-integrity gap in the replay/verify path called out in the Required Impacts as in-scope ("Hard-fork-only divergence during commit, replay, restore, or proof verification"). If a bug in JMT/SMT root computation, hot-state root computation, or the new `position_state_checkpoint_hash`/"trading-native" state root logic causes the committed state root to diverge from the correct VM-derived root (a hard-fork-class divergence), `replay_on_archive` — the tool operators/auditors rely on to detect exactly this class of bug against mainnet backups — will report a clean pass as long as the write set and events match. This defeats the purpose of the replay-verify safety net for state-commitment correctness and could let a state-root-corrupting bug go undetected until it manifests as a live chain split, since the write-set/event checks alone do not prove the state tree was assembled/committed correctly.

### Likelihood Explanation
The gap is not theoretical: it is documented in-code by the Aptos engineers themselves via the TODO, explicitly naming `replay_on_archive` as the affected consumer and stating that checkpoint-hash validation must be added "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`." This indicates the feature (trading-native/position-state roots) computing `position_state_checkpoint_hash` is actively under development and this validation gap is a known, currently real omission, not a hypothetical one — it will directly affect the correctness guarantees replay-verify is supposed to provide as soon as any state-root-affecting bug is introduced (e.g., in the position-state/hot-state commit paths that are being actively modified, as seen in `storage/aptosdb/src/state_store/tests/hot_state_snapshot.rs`).

### Recommendation
Extend `TransactionOutput::ensure_match_transaction_info` (or its caller) to independently recompute the post-transaction state/hot-state/position-state Merkle roots (or accept them as an additional parameter derived from applying the write set to a tracked state view) and assert equality against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` whenever those fields are present, before treating a replay as verified. This should be done prior to enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, as the existing TODO already indicates.

### Proof of Concept
Not directly exploitable by an unprivileged network attacker as a standalone PoC; the flaw is a missing-check condition provable purely from source:
1. `ensure_match_transaction_info` never reads or checks `state_checkpoint_hash`/`position_state_checkpoint_hash` — verifiable by inspection of the function body [1](#0-0) .
2. `replay_on_archive::Verifier::execute_and_verify` treats `Ok(())` from that call as full verification success and only records a failure on `Err` [7](#0-6) .
3. Therefore, construct (or naturally trigger via a real state-root bug) a scenario where `expected_txn_infos[idx].state_checkpoint_hash()` (or `position_state_checkpoint_hash()`) differs from the true root produced by applying `executed_outputs[idx].write_set()` to the prior state, while `write_set`/`events`/`gas_used`/`status` remain identical — `ensure_match_transaction_info` returns `Ok(())` and the divergence is not reported.

**Uncertainty note**: I could not fully trace whether some *other*, independent path (outside `ensure_match_transaction_info`) in `replay_on_archive` or `aptos_debugger.rs` separately reconstructs and checks the SMT root against the archive (the debugger's usage site wasn't fully inspected due to the iteration limit reached while reading `aptos-move/aptos-debugger/src/aptos_debugger.rs`). If such an independent state-root check exists elsewhere in that tool's pipeline, the practical severity of this specific gap would be reduced to redundancy rather than a full detection bypass. Given the explicit developer TODO calling out `replay_on_archive` by name as affected, I assess this is a genuine, currently-uncompensated gap, but recommend confirming there is no secondary state-root check before treating this as fully unmitigated.

### Citations

**File:** types/src/transaction/mod.rs (L2051-2067)
```rust
impl TransactionOutput {
    pub fn new(
        write_set: WriteSet,
        events: Vec<ContractEvent>,
        gas_used: u64,
        status: TransactionStatus,
        auxiliary_data: TransactionAuxiliaryData,
    ) -> Self {
        // TODO: add feature flag to enable
        TransactionOutput {
            write_set,
            events,
            gas_used,
            status,
            auxiliary_data,
        }
    }
```

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

**File:** storage/aptosdb/src/db/test_helper.rs (L411-451)
```rust
fn verify_snapshots(
    db: &AptosDB,
    start_version: Version,
    snapshot_versions: Vec<Version>,
    txns_to_commit: Vec<&TransactionToCommit>,
) {
    let mut cur_version = start_version;
    let mut updates: HashMap<StateKey, Option<StateValue>> = HashMap::new();
    for snapshot_version in snapshot_versions {
        let start = (cur_version - start_version) as usize;
        let end = (snapshot_version - start_version) as usize;
        assert!(txns_to_commit[end].has_state_checkpoint_hash());
        let expected_root_hash = db
            .ledger_db
            .transaction_info_db()
            .get_transaction_info(snapshot_version)
            .unwrap()
            .state_checkpoint_hash()
            .unwrap();
        updates.extend(
            txns_to_commit[start..=end]
                .iter()
                .flat_map(|x| x.write_set().write_op_iter())
                .map(|(k, op)| (k.clone(), op.as_state_value())),
        );
        for (state_key, state_value) in &updates {
            let (state_value_in_db, proof) = db
                .get_state_value_with_proof_by_version(state_key, snapshot_version)
                .unwrap();
            assert_eq!(state_value_in_db.as_ref(), state_value.as_ref());
            proof
                .verify(
                    expected_root_hash,
                    state_key.hash(),
                    state_value_in_db.as_ref(),
                )
                .unwrap();
        }
        cur_version = snapshot_version + 1;
    }
}
```

**File:** state-sync/state-sync-driver/src/bootstrapper.rs (L981-1030)
```rust
    /// The expected snapshot root for the given kind at the target version, read
    /// from the target transaction info: main state's state checkpoint hash, or
    /// the committed position state root (guaranteed present once the position
    /// stage runs, per `snapshot_kind_applies_to_target`). All kinds share the
    /// target version, so this is taken from the target output, not a storage read.
    fn expected_snapshot_root(&mut self, kind: StateKind) -> Result<HashValue, Error> {
        let transaction_output_to_sync = self.get_transaction_output_to_sync()?;
        let target_transaction_info = transaction_output_to_sync
            .get_output_list_with_proof()
            .proof
            .transaction_infos
            .first()
            .ok_or_else(|| {
                Error::UnexpectedError("Target transaction info does not exist!".into())
            })?;
        match kind {
            StateKind::MainState => target_transaction_info
                .ensure_state_checkpoint_hash()
                .map_err(|error| {
                    Error::UnexpectedError(format!(
                        "State checkpoint must exist! Error: {:?}",
                        error
                    ))
                }),
            StateKind::Position => target_transaction_info
                .position_state_checkpoint_hash()
                .ok_or_else(|| Error::UnexpectedError("Missing position state root!".into())),
        }
    }

    /// Verifies the chunk's root hash against the expected snapshot root for the
    /// kind. Resets the stream and errors on a mismatch.
    async fn verify_state_value_chunk_root(
        &mut self,
        notification_id: NotificationId,
        kind: StateKind,
        state_value_chunk_with_proof: &StateValueChunkWithProof,
    ) -> Result<HashValue, Error> {
        let expected_root_hash = self.expected_snapshot_root(kind)?;
        let chunk_root_hash = state_value_chunk_with_proof.root_hash;
        if chunk_root_hash != expected_root_hash {
            self.reset_stream(notification_id, NotificationFeedback::InvalidPayloadData)
                .await?;
            return Err(Error::VerificationError(format!(
                "The {:?} states chunk root hash: {:?} didn't match the expected hash: {:?}!",
                kind, chunk_root_hash, expected_root_hash,
            )));
        }
        Ok(expected_root_hash)
    }
```
