## Analysis Summary

The external report's bug class reduces to: **a value-tracking invariant that is silently skipped/incomplete on one code path, allowing a downstream check that should detect state divergence to pass even though the underlying committed state has actually diverged.** In the zkSync report, `_verifyDepositLimit` fails to consistently track deposits, letting the enforcement check be bypassed/broken.

I traced this pattern to Aptos's transaction-output/transaction-info comparison logic, which is the analog of "the check that's supposed to catch divergence but doesn't."

### Title
Replay/restore verification (`ensure_match_transaction_info`) silently skips state-checkpoint hash comparisons, allowing state divergence to pass as a verified match - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative comparator used by replay-verify and backup-verify tooling to confirm that a locally re-executed `TransactionOutput` matches an authenticated `TransactionInfo` (the object committed into the transaction accumulator and covered by ledger-info signatures). The function checks status, gas, write-set hash, and event-root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that actually attest to the resulting state root after a checkpoint.

### Finding Description [1](#0-0) 

The comparator verifies `status`, `gas_used`, the write-set hash against `state_change_hash`, and the event-root hash, then returns `Ok(())` — explicitly, per the in-code comment, without validating `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` against the locally computed state root.

This function is used as the integrity gate in multiple real verification flows:
- `execution/executor/src/chunk_executor/mod.rs::verify_execution`, used by chunk-executor's execution verification mode. [2](#0-1) 
- `storage/db-tool/src/replay_on_archive.rs::execute_and_verify`, the tool whose entire purpose is to re-execute historical transactions and flag divergence from the authenticated ledger. [3](#0-2) 
- `aptos-move/cli/src/commands.rs`, CLI transaction replay comparison for system transactions. [4](#0-3) 

By contrast, other authenticated verification paths in the same file (`TransactionOutputListWithProof::verify`, `TransactionListWithProof::verify`) also omit an explicit state-checkpoint-hash check against locally recomputed state — they only check write-set hash, gas, status, and event root against the proof-embedded `TransactionInfo`, relying on `TransactionInfoWithProof`/accumulator verification purely for the *authenticity* of the stored `TransactionInfo`, not for *re-execution correctness* of the state root. The state-checkpoint hash is the only field in `TransactionInfo` that binds the transaction to the resulting Jellyfish Merkle root; skipping it in the re-execution comparator means the tool can accept a `TransactionOutput` from re-execution whose resulting state differs from what was originally committed, as long as the write-set bytes and events happen to hash identically (or, more realistically, as long as the divergence occurs downstream in the checkpoint hashing behavior itself — e.g., the hot-state or position-state summary — rather than in the write set).

### Impact Explanation
This breaks the "authenticated API and proof-bearing responses must stay bound to the right ledger version, root, and object" invariant for replay/restore verification tooling. `db-tool`'s `replay_on_archive` and the chunk-executor's `verify_execution_mode` exist specifically to detect state divergence (e.g., after a VM/gas-schedule/feature change or a hard fork) by comparing re-executed output against the archived, signed `TransactionInfo`. Because the comparator does not check `state_checkpoint_hash` (and the V1-only `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`), a state-root divergence introduced by re-execution (e.g., from a state-checkpoint/hot-state computation bug, or a bug triggered specifically under COMPUTE_TRADING_NATIVE_STATE_ROOTS-style features) would not be flagged by this tool, and could report "successful replay" despite the authenticated state root having actually diverged from local execution. The comment inside the function acknowledges this exact risk in relation to `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

### Likelihood Explanation
This is a self-admitted, currently-live gap (not merely theoretical): the TODO explicitly states the checkpoint hashes are ignored and must be validated "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`." Since the feature flag exists in `types/src/on_chain_config/aptos_features.rs` and the framework (`aptos-move/framework/move-stdlib/sources/configs/features.move`), the comparator is reachable today by any replay-verify/db-tool run, and the gap becomes a real hard-fork-detection blind spot the moment state-checkpoint-affecting logic changes (position state, hot state, or trading-native state roots) without the comparator being updated in lockstep.

### Recommendation
Extend `ensure_match_transaction_info` to compute the local `state_checkpoint_hash` (and, when present, `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) from the re-executed state and assert equality against `txn_info`'s corresponding fields, mirroring how `write_set_hash`/`event_root_hash` are already checked. This should be done before any feature that changes checkpoint-hash computation (e.g., `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is enabled, and ideally the check should be unconditional so replay/restore tooling always fails loudly on state-root divergence rather than only on write-set/event divergence.

### Proof of Concept
1. Take any historical transaction range and run `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify` (or chunk-executor's `verify_execution` in `VerifyExecutionMode`) against a build where a bug or config change alters the computed state-checkpoint hash (state Merkle root, hot-state root, or position-state root) for a checkpoint transaction, while leaving the write set, events, gas, and status identical to the original commit.
2. Observe `ensure_match_transaction_info` returns `Ok(())` because it never compares `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()` against anything computed from the re-executed output. [5](#0-4) 
3. The replay/verify tool reports success even though the state root that would be committed diverges from the one originally signed into the ledger info — exactly the "authenticated output bound to the wrong root" scenario the State-Integrity gate calls out.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L692-705)
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
```

**File:** storage/db-tool/src/replay_on_archive.rs (L392-406)
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
