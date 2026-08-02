## Finding: `ensure_match_transaction_info` skips checkpoint-hash verification, allowing replay-verify to certify a divergent state root as correct

### Title
Replay-verify integrity check omits `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` comparison, masking authenticated state-root divergence - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single authoritative comparator used by chain-history replay/verification tooling to confirm that a freshly re-executed transaction produced exactly the state the network already committed and authenticated (via `TransactionInfo`, which is itself accumulator-proof-bound). The function checks status, gas used, write-set hash, and event-root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the three fields that actually attest to the Merkle root of ledger state. The code even contains a TODO admitting this gap.

### Finding Description [1](#0-0) 

`ensure_match_transaction_info` verifies:
- `status` matches [2](#0-1) 
- `gas_used` matches [3](#0-2) 
- write-set hash equals `txn_info.state_change_hash()` [4](#0-3) 
- event-root hash equals `txn_info.event_root_hash()` [5](#0-4) 

But it never compares against `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` — the fields defined on `TransactionInfoV1`/`TransactionInfoV0` that attest to the Sparse Merkle Tree/JMT root of world state at that version [6](#0-5) . The code's own comment documents this gap: [7](#0-6) 

This function is the sole state-equivalence oracle used by `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which independently re-executes historical transactions against a state view built from prior outputs and compares the freshly computed `TransactionOutput` to the authenticated (backed-up) `TransactionInfo`: [8](#0-7) 

Because `write_set_hash` is checked against `state_change_hash` (the hash of the write-set itself, not a state root), a write set that is byte-identical to what was originally applied will pass even if the *cumulative* state it produces — which depends on the write set plus all prior state, hashed into the Jellyfish Merkle root — actually diverges. The only field that would catch a divergence in the accumulated Merkle state (as opposed to a divergence purely local to one transaction's write set) is the checkpoint-hash comparison, and that comparison is entirely absent here.

By contrast, the live-commit path (`DoStateCheckpoint::run`) *does* strictly validate computed checkpoint hashes against the `TransactionInfo`-derived `known_state_checkpoints`/`known_hot_state_checkpoints`/`known_position_state_checkpoints` via `ensure!` [9](#0-8) , and `chunk_executor::update_ledger` feeds exactly those known hashes in from `txn_infos` [10](#0-9) . So the online chunk-executor commit path is protected. The gap is isolated to the offline replay-verify tool that is explicitly relied upon to catch state-divergence bugs (including bugs that would otherwise require a hard fork) across the full authenticated transaction history before they manifest in production.

### Impact Explanation
This falls squarely in the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Authenticated API or state-view output bound to the wrong version, object, or proof context" categories. `replay_on_archive` (and its `aptos-debugger`/CLI wrappers) is the tool operators and CI run to certify that re-executing the full authenticated chain history reproduces the exact same ledger state as what was originally committed — this is precisely the mechanism meant to detect nondeterminism/consensus bugs that could otherwise silently corrupt state or require an emergency hard fork. Because the state-root/hot-state-root/position-state-root fields of `TransactionInfo` are never checked, a bug that produces a byte-identical write set for a given transaction but an incorrect cumulative state root (e.g., a JMT/state-summary computation bug, a state-view resolution bug reading stale/wrong prior state, or a bug specific to the new trading-native position state) would pass replay-verify silently. This creates a false sense of security about the correctness of historical execution and could delay detection of a real state-divergence bug until it manifests as a consensus/hard-fork incident.

### Likelihood Explanation
The gap is deterministic and always present — every call to `ensure_match_transaction_info` skips these checks unconditionally, not just under some feature flag. `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/position-state and hot-state features are actively under development in this repo [11](#0-10) , increasing near-term likelihood that a state-root computation bug in these new paths would go undetected by the standard replay-verify safety net specifically at the time it's most needed (during rollout of new state-root logic).

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`-derived checkpoint hashes (when the executed transaction is a checkpoint) against `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()`, as the TODO already specifies, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or relying on replay-verify results as an integrity guarantee.

### Proof of Concept
1. Construct/mutate a historical transaction whose write set is unchanged but whose resulting Merkle state root would differ (e.g., simulate a bug in the state-summary/JMT update logic in `LedgerStateSummary::update` while leaving `write_set` itself untouched — this is externally indistinguishable from `ensure_match_transaction_info`'s check because the function never reads `self`'s locally computed checkpoint hash at all).
2. Run `storage/db-tool/src/replay_on_archive.rs`'s verifier against the corresponding chunk containing this transaction; `execute_and_verify` calls `ensure_match_transaction_info` [12](#0-11)  which only checks status/gas/write_set_hash/event_root_hash.
3. Observe the check returns `Ok(())` and reports the transaction as correctly replayed, despite an actual divergence in the authenticated state root that a correct verifier should have flagged via `txn_info.state_checkpoint_hash()`.

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

**File:** types/src/transaction/mod.rs (L2402-2416)
```rust
    /// The root hash of Merkle Accumulator storing all events emitted during this transaction.
    event_root_hash: HashValue,

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

**File:** execution/executor/src/chunk_executor/mod.rs (L373-394)
```rust
        let txn_infos = chunk_verifier.transaction_infos();
        let known_state_checkpoints = Some(
            txn_infos
                .iter()
                .map(|t| t.state_checkpoint_hash())
                .collect_vec(),
        );
        let known_hot_state_checkpoints =
            output.execution_output.hot_state_root_in_txn_info.then(|| {
                txn_infos
                    .iter()
                    .map(|t| t.hot_state_checkpoint_hash())
                    .collect_vec()
            });
        let compute_trading_native_state_roots =
            output.execution_output.compute_trading_native_state_roots;
        let known_position_state_checkpoints = compute_trading_native_state_roots.then(|| {
            txn_infos
                .iter()
                .map(|t| t.position_state_checkpoint_hash())
                .collect_vec()
        });
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L1-1)
```rust

```
