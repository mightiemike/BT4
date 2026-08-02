### Title
`TransactionOutput::ensure_match_transaction_info` skips checkpoint-hash validation, allowing replay/debugger verification to accept a diverged state root - (File: types/src/transaction/mod.rs)

### Summary
The re-execution/replay verification routine used by `db-tool replay-on-archive`, `aptos-debugger`, and the CLI's transaction-replay comparison (`aptos-move/cli/src/commands.rs`) only checks transaction status, gas used, write-set hash (`state_change_hash`), and event root hash. It explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the locally-computed state root, per an in-code TODO admission.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` at [1](#0-0)  validates a re-executed `TransactionOutput` against the persisted/expected `TransactionInfo` by comparing status, gas, write-set hash, and event root hash. Immediately before returning `Ok(())` it contains this explicit comment: [2](#0-1) 

which states verbatim that the comparator "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is called from three consumers that treat its `Ok(())` result as proof that re-execution reproduced the exact committed ledger state:
- `execution/executor/src/chunk_executor/mod.rs::verify_execution`, used by the `replay-verify` DB tool flow [3](#0-2) 
- `aptos-move/aptos-debugger/src/aptos_debugger.rs::print_mismatches` [4](#0-3) 
- `aptos-move/cli/src/commands.rs` transaction-replay comparison [5](#0-4) 

Because the write-set hash check (`state_change_hash`) only proves the *transaction's own* write set matches, and the state checkpoint hash is what actually binds the *cumulative Jellyfish Merkle state root* at a checkpoint version, skipping it means the comparator cannot detect a scenario where the individual write set is correct (or accidentally coincides) but the derived state root — as computed from the sequence of state updates applied on top of the pre-state — has diverged, e.g. due to a state-view bug, an ordering bug in applying deltas, or (per the comment) the new "trading native" position state root feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`).

### Impact Explanation
This affects a proof/authenticated-response invariant explicitly called out in scope: "committed state that differs from the correct VM result" and "authenticated API or state-view output bound to the wrong version, object, or proof context." `replay-verify` and `aptos-debugger` are the primary tools operators and auditors use to confirm that an archived/backed-up chain segment, or a locally re-executed transaction, reproduces the exact on-chain state. A silent gap in this check means a state-root divergence (whether from a software bug, backup corruption, or a malicious/corrupted archive) would not be flagged by these tools, giving false assurance of ledger integrity. This is a genuine, self-acknowledged verification gap in the repository's own state-integrity tooling, not merely a Solidity-analog reused report.

### Likelihood Explanation
The gap is unconditional in the current comparator — it always skips checkpoint-hash comparison, not just when the new trading-native feature is disabled. However, actually observing a state-root divergence in practice requires either an independent bug elsewhere in state-checkpoint computation, or feeding the tool a corrupted/forked backup archive; the check itself is a detection gap rather than a state-corruption primitive on its own. The TODO comment indicates the aptos-core maintainers are already aware of this as an open item tied to the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` rollout, which somewhat mitigates novelty but does not eliminate the exposure window until it is fixed.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash` (and, when applicable, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) computed from the locally re-executed state view against the corresponding fields of the expected `TransactionInfo`, for any transaction that is a checkpoint (i.e., where `txn_info.state_checkpoint_hash()` is `Some`). This should be enabled before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is turned on in production, per the existing TODO.

### Proof of Concept
Not applicable as a live exploit — this is a verification-logic gap, not an executable attack. Demonstration path: construct a `TransactionOutput` whose write set / events / gas / status match a given `TransactionInfo` exactly, but whose resulting state-checkpoint root (as would be computed by `DoStateCheckpoint::run`) differs from `txn_info.state_checkpoint_hash()`. Call `ensure_match_transaction_info` — it returns `Ok(())` despite the state-root mismatch, and `db-tool replay-on-archive` / `aptos-debugger` would report a successful replay.

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

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```
