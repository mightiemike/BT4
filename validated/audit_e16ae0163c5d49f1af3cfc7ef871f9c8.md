### Title
`ensure_match_transaction_info` skips verifying `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, letting replay-verify accept a diverged state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole correctness check used by `db-tool replay-on-archive` (and other replay/debug paths) to confirm that a locally re-executed `TransactionOutput` matches the authenticated, on-chain `TransactionInfo` for that version. The function explicitly checks status, `gas_used`, the write-set hash (`state_change_hash`), and the event root hash, but — per its own inline TODO — does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`.

### Finding Description [1](#0-0) 

```rust
pub fn ensure_match_transaction_info(
    &self,
    version: Version,
    txn_info: &TransactionInfo,
    expected_write_set: Option<&WriteSet>,
    expected_events: Option<&[ContractEvent]>,
) -> Result<()> {
    ...
    // TODO(trading-native): this comparator ignores the checkpoint hashes
    // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
    // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
    // replay even when the authenticated position state root diverges from
    // local execution. Validate the checkpoint hashes here before enabling
    // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
    Ok(())
}
```

The function verifies the executed status, gas used, `state_change_hash` (write-set hash), and `event_root_hash`. It never compares `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` against anything computed from the locally re-executed state. This is the only integrity check performed in `storage/db-tool/src/replay_on_archive.rs`: [2](#0-1) 

The re-executed `TransactionOutput` is compared against `expected_txn_info` solely via `ensure_match_transaction_info`, and if it returns `Ok(())`, the transaction is treated as verified, and the state-checkpoint root committed in the archive is never independently confirmed against local re-execution.

`state_checkpoint_hash` is the root of the Sparse/Jellyfish Merkle state tree at a checkpoint boundary, and per-version proofs and state-view responses are derived from and validated against this value — it is a primary state-commitment invariant of the ledger: [3](#0-2) 

Because the comparator silently skips this field, replay-verify tooling built on `ensure_match_transaction_info` will report success for a chunk even if the executor's freshly computed state-checkpoint (or the newer hot-state / "trading-native" position-state) root diverges from what is recorded (and authenticated by consensus) in the archived `TransactionInfo`. The gap is explicitly tied to the in-progress `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag work, which is present in the codebase already (`types/src/on_chain_config/aptos_features.rs`, `storage/aptosdb/src/db/aptosdb_reader.rs`), indicating the new state-root computation path exists and could feed values that this comparator would never check.

### Impact Explanation
Replay-verify (`storage/db-tool/src/replay_on_archive.rs`, backed by `ensure_match_transaction_info`) is one of the mechanisms used to detect execution/state divergence between archived, historically-committed ledger data and fresh local re-execution — including catching hard-fork-style bugs, storage corruption, or malicious archive data before it's trusted. Because the checkpoint-hash fields are excluded from comparison, a state root mismatch (e.g., from a JMT computation bug, a hot-state/position-state serialization bug, or corrupted archived snapshot data) at a checkpoint version will not be flagged as a verification failure. This directly undermines the state-commitment integrity guarantee that replay-verify is supposed to provide, and could let a wrong state-checkpoint root "pass" as validated, propagating undetected corruption or masking a genuine consensus/state divergence.

### Likelihood Explanation
This is not a hypothetical: the gap is self-documented in the source as a known incompleteness ("this comparator ignores the checkpoint hashes... so replay-verify tooling... can report a successful replay even when the authenticated position state root diverges from local execution"), and it is on a code path (`db-tool replay-on-archive`) that operates on real archived data without any privileged/trusted preconditions — it is a straightforward analysis (not exploitation) of committed history. However, whether this currently causes a live, actively wrong outcome versus a documented future risk (guarding `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, a feature that may not yet be enabled/computing divergent roots in this codebase snapshot) is not fully verifiable — I could not confirm from available context whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is currently active on mainnet or still gated off, nor find the exact producer of `position_state_checkpoint_hash` on the execution side. This limits certainty to "the invariant is provably unchecked" rather than "there is a currently-known live root divergence being masked."

### Recommendation
Extend `ensure_match_transaction_info` to independently recompute (or accept as a parameter) the local state-checkpoint root, hot-state checkpoint root, and `position_state_checkpoint_hash`, and assert equality with the corresponding fields of `txn_info` whenever those fields are `Some`, before considering a replayed transaction "verified." This should be done prior to (or as a precondition of) enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, as the inline TODO already indicates.

### Proof of Concept
Not independently reproducible from static analysis alone — this is a code-audit finding based on the explicit gap in `ensure_match_transaction_info`'s logic and its exclusive use as the correctness oracle in `replay_on_archive.rs::execute_and_verify`. A concrete PoC would require constructing/corrupting an archived `TransactionInfo`'s `state_checkpoint_hash` (or hot/position variants) while keeping `state_change_hash`/`event_root_hash`/`gas_used`/`status` correct, then running `db-tool replay-on-archive` over that range and observing that verification succeeds despite the wrong checkpoint root — this was not executed in this session due to lack of a runnable environment.

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

**File:** types/src/transaction/mod.rs (L2405-2416)
```rust
    /// The hash value summarizing all changes caused to the world state by this transaction.
    /// i.e. hash of the output write set.
    state_change_hash: HashValue,

    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,

    /// The hash value summarizing PersistedAuxiliaryInfo.
    auxiliary_info_hash: Option<HashValue>,
}
```

**File:** storage/db-tool/src/replay_on_archive.rs (L373-406)
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
        }
```
