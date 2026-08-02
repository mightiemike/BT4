This confirms the analog. `db-tool`'s `replay_on_archive` (the tool used to replay-verify historical mainnet transactions against archived data) relies exclusively on `TransactionOutput::ensure_match_transaction_info` to decide whether a re-executed transaction matches the authoritative, previously-committed result [1](#0-0) . That comparator explicitly skips the state-checkpoint / hot-state / `position_state_checkpoint_hash` fields of `TransactionInfo`, as the code itself documents [2](#0-1) .

### Title
Replay-verify tooling skips checkpoint-hash comparison, allowing divergent state roots to pass as verified - (File: types/src/transaction/mod.rs)

### Summary
`ensure_match_transaction_info` is the single source of truth used by replay/verification tooling to assert that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` recorded on-chain (and proven by the ledger accumulator). It checks status, gas, write-set hash, and event-root hash, but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` [3](#0-2) .

### Finding Description
This mirrors the bug-class in the external report: a value that should be captured/compared at a specific commitment point is instead silently omitted, so the check "passes" even though the underlying state has diverged. Here, the state Merkle root (state checkpoint hash) is the value that commits the *entire world state* at a checkpoint version, is embedded in `TransactionInfo`, and is what the transaction accumulator and ledger-info signatures ultimately authenticate. `ensure_match_transaction_info` is invoked by `replay_on_archive::Verifier::execute_and_verify` for every replayed chunk to decide pass/fail [4](#0-3) , and by `aptos-debugger` similarly. Because the comparator omits the checkpoint-hash fields, a re-execution that produces a different state root (e.g., due to a VM/state-tree divergence between the archived committed data and current execution) is reported as a successful, verified replay.

The code contains a self-documented acknowledgement of this exact gap tied to the new `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / `TRANSACTION_INFO_V1` feature that lets the position-state root be committed to `TransactionInfoV1` and consensus-verified [5](#0-4) , but the check needed to actually validate that authenticated value was never added, and the TODO explicitly warns it must be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" [6](#0-5) .

### Impact Explanation
This breaks the "committed state that differs from the correct VM result... accepted as valid" invariant and the "authenticated API/proof-bearing responses must stay bound to the right ledger version and root" invariant. Replay-verify is the primary mechanism operators and auditors use to confirm archived/mainnet history is authentic and reproducible by an independent VM execution. If the state (or hot-state / position-state) root silently diverges — from an execution bug, a storage bug, or corrupted archive data — this tooling will not detect it and will report the chunk as verified, masking a hard-fork-class divergence or ledger corruption.

### Likelihood Explanation
Likelihood is Medium: it requires an actual state-root divergence to occur (via a VM bug, non-determinism, or corrupted backup data) for this gap to matter, but if such divergence exists, this is the exact safety net that fails to catch it, and the divergence would otherwise be invisible until node/consensus-level effects manifest.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` between the produced `TransactionOutput`/checkpoint-computation result and the expected `TransactionInfo`, gated appropriately for when checkpoints are actually produced (not every transaction has a checkpoint hash). This should be done irrespective of whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, since state-checkpoint hash validation is fundamental to `replay_on_archive` and `aptos-debugger` correctness.

### Proof of Concept
1. `replay_on_archive` reads `expected_txn_info` (containing the authenticated `state_checkpoint_hash`) from backup/archive data and calls `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_infos[idx], ...)` [7](#0-6) .
2. `ensure_match_transaction_info` checks `status`, `gas_used`, `write_set_hash`, `event_root_hash` only, and returns `Ok(())` without ever inspecting `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()` [8](#0-7) .
3. Construct (or encounter) a scenario where re-execution produces the same events/write-set/gas/status but a different state (or position/hot-state) root than the archived value — the function still returns `Ok(())`, and `replay_on_archive` reports the chunk as successfully verified even though the state root differs from the authenticated committed value.

Note: I was unable to fully trace whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`TRANSACTION_INFO_V1` are currently enabled on mainnet or are still gated off; if they are not yet active, the `position_state_checkpoint_hash` portion of this gap is not yet exploitable in production, but the `state_checkpoint_hash`/`hot_state_checkpoint_hash` omission applies unconditionally to all versions of `TransactionInfo` already in use.

### Citations

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
