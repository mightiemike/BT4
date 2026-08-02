### Title
`ensure_match_transaction_info` omits `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` checks, letting replay-verify tooling accept a diverged state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the invariant used by replay/debugger tooling to assert that a locally re-executed transaction produced the exact result that was authenticated on-chain (via the transaction accumulator). It checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly skips comparing the state-checkpoint-related hashes carried by `TransactionInfoV1` (state checkpoint hash, hot-state checkpoint hash, position-state checkpoint hash), as flagged by its own `TODO(trading-native)` comment.

### Finding Description
`ensure_match_transaction_info` is defined at [1](#0-0)  and is the sole correctness gate used by three consumers: `aptos-debugger`'s mismatch printer [2](#0-1) , the `aptos move replay` CLI command for system transactions [3](#0-2) , and `storage/db-tool`'s `replay_on_archive` verification, which is the tool run against archival nodes to validate historical execution correctness across the whole chain [4](#0-3) .

The function explicitly checks `status`, `gas_used`, `write_set_hash` (against `state_change_hash`), and `event_root_hash`, but the code contains its own acknowledgment of the gap: [5](#0-4) 

Meanwhile, `TransactionInfoV1` legitimately carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as first-class fields that are populated by `DoLedgerUpdate::assemble_transaction_infos` from checkpoint outputs and folded into the accumulator leaf hash [6](#0-5) . These hashes represent the authenticated commitment to the resulting global (and hot/position) state Merkle roots at that version — a value distinct from the write-set hash, which only commits to the transaction's own deltas, not the resulting merged state tree.

Because `ensure_match_transaction_info` never compares locally-recomputed checkpoint hashes to the ones in the authenticated `TransactionInfo`, a divergence in the state-checkpoint/state-root computation path (e.g., in `DoStateCheckpoint`, the JMT root computation, or the upcoming "trading native" position-state root logic gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) would not be detected. Both `write_set_hash` and `event_root_hash` could match perfectly (the VM output/write-set itself is not corrupted) while the actual state root diverges from what is committed on-chain, and this check would still return `Ok(())`.

### Impact Explanation
This breaks the "proof/commitment-integrity" invariant required by the exercise: authenticated state-checkpoint/proof fields must survive comparison unchanged during replay/verification. `replay_on_archive` is the tool operators use to detect hard-fork-relevant divergences between historical execution and the authenticated ledger; because it delegates entirely to `ensure_match_transaction_info`, a bug elsewhere in state-checkpoint root computation (present or future, e.g. under the not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature) would silently pass replay-verification. This directly matches the in-scope impact category "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Wrong ... state proof accepted as valid," since the state-checkpoint hash is exactly the value bound into the accumulator leaf and thus into the ledger's Merkle proof structure.

### Likelihood Explanation
This is a real, currently-existing gap in the verification logic itself (not merely a theoretical extrapolation): the code comment in the same function confirms the developers are aware the checkpoint hashes are unchecked and that this must be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" [7](#0-6) . This indicates the gap is currently latent (the feature that would exercise the divergence is not yet enabled), so exploitability today requires an independent state-checkpoint-root computation bug to actually manifest a divergence; this check merely fails to catch it. I was not able to fully confirm, given tool-call limits, whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is already enabled on any active network or is purely a future/dev flag — this bears on whether the missing check is presently reachable in an authenticated real-network context versus dormant pending that feature's rollout.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in the `TransactionInfo` variant) against the corresponding locally-recomputed values, mirroring the existing pattern used for `state_change_hash` and `event_root_hash`, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any consumer relying on these fields for integrity) is enabled.

### Proof of Concept
Given the latent/gated nature of this issue, a concrete on-chain PoC is not currently constructible without a companion bug that actually produces a wrong checkpoint hash. The demonstrable proof is structural: any test that constructs a `TransactionOutput`/`TransactionInfoV1` pair with matching `write_set`/`events`/`gas_used`/`status` but a deliberately wrong `state_checkpoint_hash` (or `position_state_checkpoint_hash`) will show `ensure_match_transaction_info` returning `Ok(())` — confirming the missing check — while `replay_on_archive`'s `execute_and_verify` [8](#0-7)  would treat the replay as successful.

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

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L233-246)
```rust
    fn print_mismatches(
        txn_outputs: &[TransactionOutput],
        expected_txn_infos: &[TransactionInfo],
        first_version: Version,
    ) {
        for idx in 0..txn_outputs.len() {
            let txn_output = &txn_outputs[idx];
            let txn_info = &expected_txn_infos[idx];
            let version = first_version + idx as Version;
            txn_output
                .ensure_match_transaction_info(version, txn_info, None, None)
                .unwrap_or_else(|err| println!("{}", err))
        }
    }
```

**File:** aptos-move/cli/src/commands.rs (L2651-2655)
```rust
                if !self.skip_comparison {
                    txn_output
                        .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                        .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
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
