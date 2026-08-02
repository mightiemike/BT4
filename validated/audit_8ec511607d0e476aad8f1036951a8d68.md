### Title
`ensure_match_transaction_info` skips checkpoint-hash validation, allowing replay-verify tooling to accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the local-execution-vs-authenticated-`TransactionInfo` integrity check used by replay/verification tooling (chunk executor replay, `db-tool`'s `replay_on_archive`, and the Aptos debugger). The function validates status, gas, write-set hash, and event root hash against the trusted `TransactionInfo`, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, per its own inline TODO comment.

### Finding Description
In `types/src/transaction/mod.rs`, `ensure_match_transaction_info` checks:
- `status` [1](#0-0) 
- `gas_used` [2](#0-1) 
- write-set hash vs `state_change_hash` [3](#0-2) 
- event root hash [4](#0-3) 

but the function's own comment states it deliberately skips the checkpoint hashes: [5](#0-4) 

This means the state root/checkpoint commitment (`TransactionInfo::state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) — the actual JMT/state-tree root binding for that version — is never cross-checked against locally computed values in this code path, even though this function is used specifically to validate replayed/re-executed output against an authenticated `TransactionInfo` sourced from a proof or trusted ledger info, in `execution/executor/src/chunk_executor/mod.rs` and `storage/db-tool/src/replay_on_archive.rs`.

### Impact Explanation
If a state-checkpoint computation diverges (e.g., due to a future bug in JMT construction, hot-state, or the position-state checkpoint path referenced by the TODO's "trading-native"/`COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature), replay-verify tooling built on `ensure_match_transaction_info` would report success even though the locally computed state root differs from the authenticated on-chain state root. This directly violates the required invariant that "committed state that differs from the correct VM result... must not be accepted," since the verification gate that is supposed to catch such divergence silently ignores the checkpoint-hash fields.

### Likelihood Explanation
This is a genuine, currently-existing gap acknowledged by the code's own TODO rather than a hypothetical one; it does not require any external state or attacker input, only a legitimate discrepancy between local state-checkpoint computation and the authenticated `TransactionInfo`. However, the impact is contingent on such a discrepancy occurring elsewhere (e.g., in the not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / trading-native or hot-state paths); on its own this function is a verification-tooling gap rather than a live consensus-breaking bug, since normal transaction execution/commit does not go through this specific comparator to reach consensus.

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in the `TransactionInfo` variant) against the locally computed values before enabling any feature (e.g., `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that depends on this comparator for correctness guarantees, so replay/verify tooling cannot silently accept a divergent state root.

### Proof of Concept
Not applicable as a runnable exploit — this is a code-review-level gap in an integrity check identified via the function's own TODO comment: [6](#0-5) . I was unable to fully trace, within the available iterations, whether any of the three checkpoint hashes are independently validated elsewhere in the `chunk_executor` or `replay_on_archive` call sites before invoking `ensure_match_transaction_info`; confirming that would require reading `execution/executor/src/chunk_executor/mod.rs` and `storage/db-tool/src/replay_on_archive.rs` around their call sites, which I did not get to inspect before running out of tool iterations.

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
