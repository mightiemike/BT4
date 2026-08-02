### Title
`TransactionOutput::ensure_match_transaction_info` never validates state/hot-state/position checkpoint hashes, letting `replay-verify` and archive-replay tooling accept a divergent state root — ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` (the function used by replay/verification tooling to confirm a freshly-executed `TransactionOutput` matches the authenticated `TransactionInfo` from storage/backup) checks transaction status, gas used, write-set hash (`state_change_hash`), and event root hash — but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This gap is called out in the code itself via a `TODO(trading-native)` comment, but is not actually gated behind the referenced feature flag; the check is simply absent in the checked-in code path used today.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  is the canonical equality check between a locally re-executed `TransactionOutput` and the `TransactionInfo` that was persisted/backed-up (and which is bound to the accumulator root committed by consensus). It verifies:
- execution status [2](#0-1) 
- gas used [3](#0-2) 
- write-set hash vs `state_change_hash` [4](#0-3) 
- event root hash [5](#0-4) 

It never compares `txn_info.state_checkpoint_hash()`, the hot-state checkpoint hash, or `position_state_checkpoint_hash` against anything computed from the replay. The comment directly preceding the `Ok(())` return states this is a known omission: [6](#0-5) 

This function is consumed by exactly the tools whose job is to catch state-commitment divergence:
- `execution/executor/src/chunk_executor/mod.rs` (chunk executor state-sync/backup verification path)
- `aptos-move/aptos-debugger/src/aptos_debugger.rs` (debugger replay)
- `aptos-move/cli/src/commands.rs` (CLI replay/verify commands)

All of these callers rely on `ensure_match_transaction_info` returning `Ok(())` to mean "the replayed output is consistent with the authenticated ledger data." Because the state/hot-state/position checkpoint hashes are silently skipped, a state root produced by local execution that diverges from the checkpoint hash embedded in the authenticated `TransactionInfo` (which is itself accumulator-proof-bound, see `types/src/proof/definition.rs` `verify_transaction_info`) will not be detected by this comparator. The write-set hash and event root hash equality alone are insufficient to guarantee the resulting Merkle/JMT state root matches, since the state root depends on how the write-set is applied against prior state (including hot-state and, per the comment, "position state"), not solely on the write-set's own hash.

### Impact Explanation
This breaks the "committed state that differs from correct VM result... accepted as valid" invariant specifically for the tooling meant to catch it: `db-tool replay-on-archive` (`storage/db-tool/src/replay_on_archive.rs`, `Verifier::execute_and_verify`), the CLI verify commands, and the aptos-debugger. If a bug in execution, in state-checkpoint hash computation (`do_ledger_update.rs`'s `assemble_transaction_infos`), or in the write-set-to-state-value application logic caused a wrong state/hot-state/position root to be produced while the write-set hash and event hash still happened to match (or where the divergence occurs purely in derived checkpoint state, e.g. hot state or position-state, which are not part of the write set itself), replay-verify would report success despite the local execution having produced a different ledger state than what was actually committed to mainnet. This is a high-impact proof/verification-integrity gap because it defeats the explicit safety net designed to catch state divergence during archive replay and hard-fork/upgrade verification, potentially masking exactly the class of bug (VM/state-application divergence) that replay-verify exists to catch.

### Likelihood Explanation
Likelihood of exploitation as a deliberate attack is low (this isn't attacker-triggerable through normal transaction submission), but likelihood of the gap mattering is directly tied to how much replay-verify/db-tool is trusted as the safety check before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or during general operational replay-verify runs (`testsuite/replay-verify/*`, CI). The code comment explicitly acknowledges this must be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`," confirming the maintainers are aware the check is currently missing and load-bearing for a future/adjacent feature. As shipped, any latent state-checkpoint-hash divergence bug (in native "position" state or hot-state areas) would go undetected by every caller of `ensure_match_transaction_info`.

### Recommendation
Extend `ensure_match_transaction_info` to compute the state checkpoint hash (and hot-state / position-state checkpoint hash when applicable) from the locally re-executed state and assert equality with `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`, mirroring the existing `ensure!` pattern used for `write_set_hash` and `event_root_hash`. This should be done unconditionally (or at minimum whenever these hashes are present, i.e., `Some(...)`), not deferred behind the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag, since replay/verify tooling should never silently skip an available authenticated checkpoint comparison.

### Proof of Concept
Not directly exploitable via a PoC transaction since this is a verification-logic gap rather than a state-transition bug; demonstrating impact requires: (1) a (hypothetical or injected) execution/state-application divergence that changes the resulting state/hot-state root while leaving the transaction's own write-set hash and event hash unchanged, and (2) showing `db-tool replay-on-archive` / CLI verify / aptos-debugger report success despite the divergence. I was not able to fully verify within available iterations whether any other independent check (e.g., inside `chunk_executor/mod.rs`'s call site or `execute_and_verify` in `replay_on_archive.rs`) separately re-validates the state checkpoint hash outside of `ensure_match_transaction_info`; a full read of those call sites (their content did not render fully in the tool output) is needed to confirm there is no redundant check elsewhere that would mitigate this gap. I recommend a follow-up Devin session with file access to fully trace `chunk_executor/mod.rs` and `replay_on_archive.rs::execute_and_verify` to confirm no supplementary checkpoint-hash validation exists.

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
