## Analog Found

### Title
`ensure_match_transaction_info` skips state-checkpoint / hot-state / position-checkpoint hash verification, letting silent state divergence pass replay-verify - (File: `types/src/transaction/mod.rs`)

### Summary
The bug-class from the external report is: "a required integrity check is weakened/omitted, allowing an unsafe state to be accepted as valid." In Aptos, the analogous check is `TransactionOutput::ensure_match_transaction_info`, which is the function used by replay/verification tooling to confirm that locally re-executed transaction output matches the authenticated `TransactionInfo` recorded on-chain. This function deliberately omits comparison of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, as the code comment itself acknowledges.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` at [1](#0-0)  checks status, gas used, write-set hash (`state_change_hash`), and event root hash against the given `TransactionInfo`, but explicitly does **not** check the state-checkpoint-related hashes: [2](#0-1) 

The comment in the code states this outright: "this comparator ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is used in two real verification paths that are meant to detect ledger divergence:
1. `execution/executor/src/chunk_executor/mod.rs::verify_execution`, called by `TransactionReplayer::enqueue_chunks`, used for backup restore verification (`storage/backup/backup-cli`) — [3](#0-2) .
2. `storage/db-tool/src/replay_on_archive.rs::execute_and_verify`, the dedicated "replay-verify" tool that re-executes historical mainnet/testnet transactions and confirms they match the authenticated write set/state — [4](#0-3) .

By contrast, the actual online state-sync commit path (`execution/executor/src/chunk_executor/mod.rs::update_ledger`) does independently recompute and compare `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` against the proof-carried `TransactionInfo` via `DoStateCheckpoint`'s `known_state_checkpoints`/`known_hot_state_checkpoints`/`known_position_state_checkpoints` parameters — [5](#0-4) . So the live consensus/state-sync commit path is protected; only the offline replay-verify/backup-verify tooling has the gap.

### Impact Explanation
If the VM's state-checkpoint computation (Sparse Merkle Tree root, hot-state root, or the new "position state" root used by the trading-native feature) silently diverges from the historically recorded value — e.g., due to a non-determinism bug, an unintended protocol change, or a bug specific to `COMPUTE_TRADING_NATIVE_STATE_ROOTS` — both `replay_on_archive` and the backup-restore `TransactionReplayer::verify_execution` path will report success even though the locally recomputed root does not match the authenticated on-chain state root. This is exactly the "hard-fork-only divergence during commit, replay, restore, or proof verification" category called out in the impact gate: these tools exist specifically to catch state-root divergence across historical replay (used to validate correctness before hard forks, feature rollouts, and to gate backup-restore integrity), and the gap in `ensure_match_transaction_info` defeats that safety net for the state root fields.

### Likelihood Explanation
This is not an attacker-triggered exploit in the traditional sense — it is a latent gap that only manifests when a genuine state-checkpoint-hash divergence bug exists elsewhere (e.g. a VM/state-merkle nondeterminism bug). The comment in the source confirms the aptos team is already aware ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"), indicating this is a known, unresolved gap. The live consensus/state-sync path is unaffected (it has its own separate checkpoint verification), which limits the blast radius to backup-restore verification and the `replay_on_archive` diagnostic tool, but those tools are the primary mechanism for catching exactly this class of divergence before or during network upgrades.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present) between the locally computed output and `txn_info`, mirroring the checks already performed in the live `update_ledger`/`DoStateCheckpoint` path. This should be done, as the in-repo comment states, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, to ensure replay-verify and backup-restore correctly detect state-root divergence.

### Proof of Concept
Conceptual (no runtime environment available to execute):
1. Construct a `TransactionOutput` whose `write_set`, `events`, `gas_used`, and `status` match a given `TransactionInfo`, but whose state after applying the write set would produce a different Sparse-Merkle/hot-state/position-state root than the `state_checkpoint_hash` recorded in that `TransactionInfo` (this can happen from a genuine VM/state-tree bug, not from write-set tampering, since the write set itself is checked).
2. Call `ensure_match_transaction_info(version, txn_info, Some(write_set), Some(events))` — per [1](#0-0) , this returns `Ok(())` because none of the checkpoint hash fields are compared.
3. Run this scenario through `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` ( [4](#0-3) ) or the backup-restore `verify_execution` path ( [3](#0-2) ) — both report success/no error, masking the divergence.

**Caveat**: I was unable to execute this in a live environment; the finding is based on static code review and the explicit acknowledgment comment left in the source. The severity depends on whether an actual state-root nondeterminism bug currently exists elsewhere in the VM/state-merkle code (not confirmed here) — absent such a bug, this gap is a latent detection weakness rather than an active state-corruption vulnerability.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L373-413)
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
        let position_persisted = compute_trading_native_state_roots
            .then(|| ProvablePositionStateSummary::new_persisted(self.db.reader.as_ref()))
            .transpose()?;
        let state_checkpoint_output = DoStateCheckpoint::run()
            .execution_output(&output.execution_output)
            .parent_state_summary(&parent_state_summary)
            .persisted_state_summary(&ProvableStateSummary::new_persisted(
                self.db.reader.as_ref(),
            )?)
            .maybe_known_state_checkpoints(known_state_checkpoints)
            .maybe_known_hot_state_checkpoints(known_hot_state_checkpoints)
            // Parent position summary is chained across chunks by the commit
            // queue (seeded from the pre-committed position tip); the persisted
            // base supplies cold-key proofs. The known-hash check validates the
            // computed root against the committed TransactionInfos.
            .maybe_parent_position_state_summary(parent_position_state_summary.as_ref())
            .maybe_persisted_position_state_summary(position_persisted.as_ref())
            .maybe_known_position_state_checkpoints(known_position_state_checkpoints)
            .build()?;
```

**File:** execution/executor/src/chunk_executor/mod.rs (L692-706)
```rust
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

**File:** storage/db-tool/src/replay_on_archive.rs (L392-405)
```rust
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
