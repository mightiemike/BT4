### Title
`TransactionOutput::ensure_match_transaction_info` skips validating `position_state_checkpoint_hash`, letting replay/restore accept a divergent authenticated position-state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single comparator used by `db-tool`'s `replay_on_archive`, `aptos-debugger`, the Move CLI, and `execution/executor/src/chunk_executor/mod.rs` to check a locally re-executed `TransactionOutput` against the authenticated `TransactionInfo` fetched from storage/proof. The function validates status, gas, write-set hash, and event-root hash, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or the newly introduced `position_state_checkpoint_hash` fields carried in `TransactionInfoV1`.

### Finding Description
`ensure_match_transaction_info` computes and compares only three commitments derived from the `TransactionOutput`: the kept status, gas used, write-set hash (`state_change_hash`), and the event root hash. [1](#0-0) 

The function's own comment acknowledges the gap: the state/hot-state checkpoint hash and the `position_state_checkpoint_hash` (introduced for the "trading-native" position state feature, `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) are never checked here, even though `TransactionInfoV1` carries a `position_state_checkpoint_hash` field that is supposed to authenticate the Jellyfish-Merkle-like position state root: [2](#0-1) 

That field is populated during normal block execution by `DoStateCheckpoint::compute_position_checkpoint`, which folds native "position" writes into a separate Merkle-summarized ledger state distinct from the main state tree: [3](#0-2) 

and is wired into the persisted `TransactionInfo` at commit time via `assemble_transaction_infos`: [4](#0-3) 

`ensure_match_transaction_info` is invoked from `execution/executor/src/chunk_executor/mod.rs` (state-sync / restore replay), `storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs`. In all of these call sites, the comparator is treated as the authority on whether locally-computed output "matches" the authenticated `TransactionInfo` sourced from a proof or an archived ledger. Because the comparator never re-derives or checks `position_state_checkpoint_hash` (nor `state_checkpoint_hash`/`hot_state_checkpoint_hash`), a corrupted or incorrectly-restored position-state tree (or main state-checkpoint tree) can diverge from the authenticated root while `ensure_match_transaction_info` still returns `Ok(())`.

### Impact Explanation
Once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled on-chain (the feature flag exists and drives `execution_output.compute_trading_native_state_roots`), replay-verify and restore tooling built on `ensure_match_transaction_info` — most notably `db-tool`'s `replay_on_archive`, which exists specifically to catch consensus/state divergence between local execution and the authenticated chain history — can report a "successful" replay even though the locally-computed position-state root differs from the one authenticated by validator signatures. This is exactly the "authenticated API/state-view output bound to the wrong version/root" and "hard-fork-only divergence during commit/replay/restore" class of impact: a node or auditor relying on this comparator would falsely believe the local ledger state is consistent with the network's committed state when the underlying committed position-state data has actually diverged, undermining the integrity guarantee that the checkpoint hash is supposed to provide.

### Likelihood Explanation
Low likelihood today, because `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is a feature flag whose enablement status could not be confirmed as active on mainnet from this scoped code (it exists in `types/src/on_chain_config/aptos_features.rs` and `move-stdlib/sources/configs/features.move` but I was unable to determine its default/activation state within available context). The bug is real and self-acknowledged in the code (`TODO(trading-native)` comment) but is only exploitable/observable once that feature is turned on and something causes the position-state computation to diverge (e.g., a restore bug, a JMT/summary computation bug elsewhere, or a bootstrapping edge case). This is a latent gap in a defense-in-depth check rather than a directly triggerable state-corruption bug by itself.

### Recommendation
Extend `TransactionOutput::ensure_match_transaction_info` to also validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` whenever they are `Some` on the authenticated `TransactionInfo`, using the equivalent locally-recomputed checkpoint hashes (as already computed by `DoStateCheckpoint::get_state_checkpoint_hashes` during normal execution) before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled in production, so that `replay_on_archive` and other consumers of this comparator cannot silently accept a divergent, authenticated checkpoint root.

### Proof of Concept
Not independently reproducible from static analysis alone: the divergence requires (1) `COMPUTE_TRADING_NATIVE_STATE_ROOTS` to be active, and (2) some other bug or bootstrapping inconsistency that causes a node's locally computed `position_state_summary`/root to differ from the one baked into the authenticated `TransactionInfoV1.position_state_checkpoint_hash`. What can be shown directly from the code is that even under such divergence, `ensure_match_transaction_info` at [5](#0-4)  would still return `Ok(())`, since it only compares status, gas, write-set hash, and event-root hash and never touches `state_checkpoint_hash`/`position_state_checkpoint_hash`.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L62-83)
```rust
        let (position_state_summary, position_state_checkpoint_hashes) =
            if execution_output.compute_trading_native_state_roots {
                let persisted = persisted_position_state_summary
                    .expect("persisted position summary required when feature on");
                let (summary, hashes) = Self::compute_position_checkpoint(
                    execution_output,
                    parent_position_state_summary,
                    persisted,
                    known_position_state_checkpoints,
                )?;
                (Some(summary), Some(hashes))
            } else {
                (None, None)
            };

        Ok(StateCheckpointOutput::builder()
            .state_summary(state_summary)
            .state_checkpoint_hashes(state_checkpoint_hashes)
            .maybe_hot_state_checkpoint_hashes(hot_state_checkpoint_hashes)
            .maybe_position_state_summary(position_state_summary)
            .maybe_position_state_checkpoint_hashes(position_state_checkpoint_hashes)
            .build())
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L95-121)
```rust
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
