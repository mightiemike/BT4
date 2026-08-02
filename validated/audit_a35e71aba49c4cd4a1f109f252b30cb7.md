### Title
`ensure_match_transaction_info` skips state-checkpoint hash verification, letting replay-verify accept a diverged JMT state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) is the function used by `db-tool`'s `replay_on_archive` verifier [1](#0-0)  and by the CLI/debugger replay path [2](#0-1)  to check that a freshly re-executed `TransactionOutput` matches the authenticated `TransactionInfo` fetched from the archive/ledger. It checks status, gas, write-set hash (`state_change_hash`), and event root hash, but by its own admission never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`.

### Finding Description
The function's own comment states the gap explicitly: [3](#0-2) 

Walking through the checks performed: [4](#0-3) 

`write_set_hash` (state_change_hash) only proves the *delta* (write set) produced by the transaction matches what was recorded — it says nothing about whether applying that write set to the correct base state (JMT) yields the `state_checkpoint_hash` that was actually recorded in the authenticated `TransactionInfo`/accumulator at commit time. The `state_checkpoint_hash` (and, for V1, `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) is the field that binds the *entire accumulated Sparse-Merkle/Jellyfish-Merkle root* to that ledger version — it is exactly the "wrong accumulator root ... accepted as valid" class of bug called out by the task's scope. `ensure_match_transaction_info` never re-derives or compares this root at all.

This routine is invoked directly by the replay-verify tool that is the standard mechanism for validating that a full re-execution of archived history reproduces the committed ledger state (`replay_on_archive::Verifier::execute_and_verify`, which is the tool's core correctness gate) [5](#0-4) . If a bug anywhere in a Move-VM commit path (Jellyfish Merkle update logic, resource-group squash, sharded state merge, etc.) produces a correct write set (same bytes) but an incorrect derived state root — e.g., because state was applied to a wrong base version, a stale JMT node, or a resource-group merge bug corrupts unrelated keys not present in the write set diff — `ensure_match_transaction_info` will report success. Node operators, auditors, and hard-fork validators who rely on `replay_on_archive`/`db-tool replay-verify` to detect ledger divergence get a **false positive**: the tool asserts state integrity without ever validating the actual state root that is durably committed and served to clients (state proofs, `get_state_value_with_proof_by_version`, etc.).

Note that this is distinct from the block-executor's live `DoStateCheckpoint`/`assemble_transaction_infos` path, which *does* compute and (when known hashes are supplied) validate `state_checkpoint_hash` during normal commit [6](#0-5) . The gap is specifically in the standalone comparator used by replay/debug tooling that re-executes transactions outside of the full chunk-executor pipeline (VM debugger, CLI transaction replay, and `replay_on_archive`), where no independent accumulator/root recomputation exists.

### Impact Explanation
This breaks a proof/commitment invariant that the task's scope explicitly requires: "Committed state that differs from the correct VM result... accepted as valid" and "Authenticated API or state-view output bound to the wrong version, object, or proof context." A tool whose entire purpose is to catch state divergence (used for validating archive integrity, potential hard-fork audits, and node bootstrapping/backup verification confidence) can systematically miss state-root corruption as long as the write-set bytes and events happen to match. This is a high-severity gap for auditing/replay-verify correctness, though it is a detection/verification gap rather than a way to directly corrupt live consensus-committed state (the live block-execution path still computes and checkpoints state normally).

### Likelihood Explanation
The comment indicates this was a known, intentional simplification ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), suggesting the authors are aware but treat it as pending work tied to a specific upcoming feature. However, as written, the gap silently applies unconditionally to *all* callers of `ensure_match_transaction_info` today (`state_checkpoint_hash` itself, not just the trading-native fields, is unchecked), regardless of whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled. Any latent state-application bug (independent of this report) that preserves write-set bytes but corrupts other state would go undetected by every current user of this function.

### Recommendation
In `ensure_match_transaction_info`, recompute the local state-checkpoint hash (via the same state-checkpoint output path used by `DoStateCheckpoint`) and compare it against `txn_info.state_checkpoint_hash()` (and `hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()` where applicable) before returning `Ok(())`, mirroring the `state_change_hash`/`event_root_hash` checks already present. At minimum, gate all replay-verify call sites (`replay_on_archive`, CLI/debugger replay) behind an explicit state-root check so silent divergence cannot be reported as a successful replay.

### Proof of Concept
Not applicable as a runnable exploit — the finding is a verification-logic gap demonstrated purely by code inspection:
1. `ensure_match_transaction_info` compares only `status`, `gas_used`, `write_set` hash, and `event_root_hash` [7](#0-6) .
2. `replay_on_archive::Verifier::execute_and_verify` calls this as its sole per-transaction correctness check after re-executing the block [5](#0-4) .
3. Construct (conceptually) a `TransactionOutput` whose `write_set` bytes and `events` match the archived `TransactionInfo`, but whose the pre-state used to derive `state_checkpoint_hash` was subtly wrong (e.g., a stale JMT snapshot); `ensure_match_transaction_info` returns `Ok(())` despite the state root being wrong, and `replay_on_archive` reports a clean/successful replay for that range.

Because I could not fully load `storage/db-tool/src/replay_on_archive.rs` lines 136-349 (the read tool returned blank content, likely due to index/size limits truncating that portion of the file), I was unable to fully confirm there is no other independent state-root check performed elsewhere in the `Verifier`'s surrounding driver code before/after `execute_and_verify` is called. I recommend starting a full Devin session with filesystem access to inspect that full file and confirm whether any accumulator/state-root recomputation exists elsewhere in the replay pipeline that would mitigate this gap.

### Citations

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

**File:** aptos-move/cli/src/commands.rs (L2651-2655)
```rust
                if !self.skip_comparison {
                    txn_output
                        .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                        .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
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
