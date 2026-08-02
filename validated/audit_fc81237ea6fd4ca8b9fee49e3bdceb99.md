Based on my investigation, I found a concrete, code-acknowledged integrity gap, but I could not fully verify its live-path exploitability within the remaining budget, so I'll report it precisely bounded by what I confirmed.

### Title
`TransactionOutput::ensure_match_transaction_info` skips checkpoint-hash validation, allowing replay/verification tooling to certify a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used to assert that a locally re-executed `TransactionOutput` matches a trusted, proof-carrying `TransactionInfo` during chunk replay/verification. It checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap called out in the code's own comment.

### Finding Description [1](#0-0) 

The function verifies `status`, `gas_used`, the write-set hash against `txn_info.state_change_hash()`, and the event root hash against `txn_info.event_root_hash()`, but the trailing comment states verbatim that "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution," with an explicit TODO to fix this "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`."

This function is invoked from `execution/executor/src/chunk_executor/mod.rs`'s `verify_execution`, which drives `TransactionReplayer::remove_and_replay_epoch` — the tool used to replay historical/backup transactions against locally re-executed output and confirm they match committed `TransactionInfo`s [2](#0-1) . It's also referenced from `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs` (both offline/dev tooling contexts).

Because the state-checkpoint-family hashes are excluded from the comparison, a divergence between the locally computed state/hot-state/position state roots and the authenticated `TransactionInfo` roots in the backup/archive would not be flagged by this check — the replay would be reported "successful" despite the underlying state tree differing.

### Impact Explanation
This weakens a specific proof-binding invariant: "committed TransactionInfo must fully attest to the resulting state" is not fully enforced by this verifier. If the underlying state-checkpoint computation ever diverges (e.g., a bug in state-checkpoint or trading-native-state-root computation, or a subtly corrupted archive), `replay_on_archive`-style verification would still report success, masking a real divergence in committed ledger state. This matters for detecting hard-fork-only divergence during replay/verification, one of the explicitly in-scope impact categories.

### Likelihood Explanation
This is a **latent** gap rather than an actively exploitable path today: the comment indicates it's a known, intentional limitation gated behind not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` functionality, and the checked callers (`aptos-debugger`, CLI replay tooling, and chunk-executor's `verify_execution` for epoch replay verification) are offline/verification tooling rather than the mainnet consensus commit path. I could not confirm, within the available search budget, whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or the hot-state/state-checkpoint roots it guards) is already active on any live network, nor could I fully trace whether any consensus-critical commit path (as opposed to replay/verification-only paths) relies on this same comparator for acceptance of state.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present/known) against the locally computed equivalents before declaring a replayed transaction output as matching, per the TODO already present in the code.

### Proof of Concept
Not independently reproduced — this finding is based on the function's implementation and its own acknowledging comment [3](#0-2) , not on a runnable exploit against a live commit path. I was unable to confirm within this session whether the corresponding checkpoint-root feature is active on mainnet or reachable via a non-privileged, consensus-critical path, so I present this as a documented gap for further verification rather than a confirmed, weaponizable mainnet bug.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L684-707)
```rust
        // not `zip_eq`, deliberately
        for (version, txn_out, txn_info, write_set, events) in multizip((
            begin_version..end_version,
            &execution_output.to_commit.transaction_outputs,
            transaction_infos.iter(),
            write_sets.iter(),
            event_vecs.iter(),
        )) {
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
        Ok(end_version)
```
