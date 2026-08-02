Confirmed: `db-tool`'s `replay_on_archive` (`storage/db-tool/src/replay_on_archive.rs:392`) is the mainnet-facing archive-replay-verification tool, and it relies exclusively on `TransactionOutput::ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) to decide whether a locally re-executed transaction matches the authenticated `TransactionInfo` pulled from backup storage.

### Title
Replay-verify accepts corrupted native-position / hot-state checkpoint roots as valid transaction info — (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative comparator used by archive replay-verification (`storage/db-tool/src/replay_on_archive.rs`) to confirm that locally re-executed VM output matches the `TransactionInfo` fetched from a backup/archive. The function checks status, gas used, write-set hash, and event root hash, but never checks `hot_state_checkpoint_hash` or `position_state_checkpoint_hash` — two fields carried by `TransactionInfoV1` that are the accumulator-committed roots of the hot-state and native-position Jellyfish Merkle trees.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates exactly four properties between a computed `TransactionOutput` and an authenticated `TransactionInfo`: status, gas used, write-set hash (`state_change_hash`), and event root hash. It does not read or compare `txn_info.hot_state_checkpoint_hash()` or `txn_info.position_state_checkpoint_hash()` at all. The code even contains a self-documenting `TODO(trading-native)` comment acknowledging: "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

The sole caller that performs this check on real data is `execute_and_verify` in [2](#0-1) , which calls `ensure_match_transaction_info` on every replayed transaction and only reports a version as failed if this function returns an error. Since these hash fields are committed to `TransactionInfoV1` and folded into the transaction accumulator (per `execution/executor/src/workflow/do_ledger_update.rs:95-121`), they are part of the authenticated, hard-fork-consensus-critical ledger state once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / `HOT_STATE_ROOT_IN_TXN_INFO` features are enabled. A backup/archive file whose bundled `TransactionInfo.position_state_checkpoint_hash` (or `hot_state_checkpoint_hash`) does not match what local re-execution actually produces — whether due to storage corruption, a malicious/compromised backup source, or a divergent-execution bug in the native-position/hot-state pipeline — will still pass `ensure_match_transaction_info` and be reported by `replay_on_archive` as a verified, correct replay.

### Impact Explanation
This breaks the state-integrity guarantee that "authenticated proof-bearing responses/verification results must stay bound to the correct root." An operator or auditor running `replay_on_archive`/replay-verify tooling against an archive to confirm the historical native-position (order book / margin / collateral) state root is correct will get a false positive "success" result even when the position-state Jellyfish Merkle root is corrupted or has silently diverged from actual execution. Since native-position state underlies on-chain trading primitives gated by `NATIVE_POSITION`/`NATIVE_ORDERBOOK`/`NATIVE_COLLATERAL` features, an undetected divergence here means downstream consumers of "verified" archives (fork investigations, state-consistency audits, disaster-recovery restores validated via replay-verify) could trust ledger data whose committed state root does not match the correct VM result — a state-commitment/proof-integrity failure exactly in scope of the gate criteria.

### Likelihood Explanation
The bug requires no attacker privilege: it is a straightforward code omission independently confirmed by the in-repo `TODO(trading-native)` comment. It will trigger deterministically any time `replay_on_archive` is used to verify transaction ranges that carry a divergent `hot_state_checkpoint_hash` or `position_state_checkpoint_hash` (i.e., once the trading-native / hot-state features are enabled on the target chain). The comparator's gap is total (zero comparisons performed on these fields), not a partial/rare-edge-case rounding issue, so any divergence in these roots is guaranteed to be missed.

### Recommendation
Extend `ensure_match_transaction_info` to also recompute and compare `hot_state_checkpoint_hash` and `position_state_checkpoint_hash` against `txn_info`'s values whenever the corresponding fields are present (`TransactionInfo::V1` variants), gated appropriately behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / `HOT_STATE_ROOT_IN_TXN_INFO` so pre-feature history is unaffected. This must be done before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` on any network where replay-verify results are relied upon for integrity guarantees, exactly as the existing TODO already flags.

### Proof of Concept
Conceptual PoC (no local repro possible without a live devnet with trading-native features on): construct a backup/archive transaction chunk where the bundled `TransactionInfoV1.position_state_checkpoint_hash` for some version is replaced with an arbitrary/incorrect `HashValue` while status, gas, write-set hash, and event root hash remain correct. Run `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::verify` over that version range; `execute_and_verify` at [3](#0-2)  will call `ensure_match_transaction_info`, which returns `Ok(())` despite the tampered position-state root, and the tool reports zero failed transactions for that range.

Note: I was unable to independently execute this against a running trading-native-enabled node in this environment (no filesystem/terminal access), so the PoC is based on direct code-path tracing rather than an executed test; the described input→gap→output behavior is derived directly from the cited source.

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
