## Title
`ensure_match_transaction_info` skips checkpoint-hash validation, allowing state-sync/replay to accept a `TransactionOutput` whose state root diverges from local execution - ([File: types/src/transaction/mod.rs])

## Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity gate used when applying a chunk of transactions/outputs received from a peer or backup (state sync, replay-verify tooling) against the authenticated `TransactionInfo` carried in the ledger's proof. It validates status, gas, write-set hash, and event-root hash, but its own code comment documents that it deliberately skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`.

## Finding Description
`ensure_match_transaction_info` at [1](#0-0)  checks:
- execution status,
- gas used,
- write-set hash vs `txn_info.state_change_hash()`,
- event-root hash vs `txn_info.event_root_hash()`,

but never compares the locally-computed state (or hot-state / native-position) checkpoint root against `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`. The function's own comment states this explicitly: [2](#0-1) 

This check is invoked from `execution/executor/src/chunk_executor/mod.rs`, which is the chunk-executor's replay/state-sync path that re-executes transactions and cross-checks the result against the `TransactionInfo` that arrived bundled with an accumulator proof (from a peer or a backup). Because the comparison silently omits the state-checkpoint-hash family of fields, a chunk (or replay tool invocation) whose write-set/event hashes match but whose actual post-state (the Sparse-Merkle/Jellyfish state root, or the new native-position root once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled) diverges from what is recorded in the authenticated `TransactionInfo` will still pass this check.

## Impact Explanation
This breaks the "committed state must match the correct VM result" and "authenticated proof output must stay bound to the right state root" invariants required by the State-Integrity Gate: a divergent state root (bug in execution, storage layer, or a maliciously/incorrectly crafted backup/state-sync response with matching write-set/event hashes but different underlying state root) would not be caught by this specific verification function even though it is explicitly relied upon by the chunk executor and by db-tool's `replay_on_archive` to assert execution correctness during replay. The TODO in the code acknowledges this as a known gap gating a not-yet-enabled feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`), which is exactly analogous to the external report's bug class — a state that should be validated/closed by one specific check is silently skipped, letting a corrupted/incomplete state persist as if it were valid.

## Likelihood Explanation
Today, `state_checkpoint_hash` mismatches for the *existing* `state_checkpoint_hash`/`hot_state_checkpoint_hash` fields are already silently unchecked here regardless of feature flags — the comment only calls out that this must be fixed *before* enabling the new `position_state_checkpoint_hash` validation, but it does not gate the pre-existing state/hot-state checkpoint hash omission behind any flag. This means today, on mainnet, `ensure_match_transaction_info` already never verifies that a re-executed chunk's state-checkpoint root matches the authenticated `TransactionInfo`'s `state_checkpoint_hash`. This lowers confidence in replay-verify/chunk-executor correctness checks as a defense against state-root corruption bugs elsewhere in the stack, though it is not by itself an attacker-triggerable state corruption — it removes a safety net rather than directly causing the bad state.

## Recommendation
Add explicit comparisons in `ensure_match_transaction_info` between the locally computed state/hot-state/position state checkpoint hashes and the corresponding fields in `txn_info`, gated appropriately by whether those hashes were computed in the current execution context (i.e., only compare when the checkpoint hash is actually produced for this transaction), before this function is relied upon as a correctness oracle in chunk-executor/replay-verify flows.

## Proof of Concept
Not independently reproducible as an attacker-triggerable exploit from this repo snapshot alone — the finding is a code-level proof of a validation gap (self-documented via the `TODO(trading-native)` comment) rather than a demonstrated live exploit. I could not fully trace how the `known_state_checkpoints` values are cross-checked in `execution/executor/src/chunk_executor/mod.rs` (the call site) within the remaining budget, so I cannot definitively state whether an upstream check elsewhere fully compensates for this gap in the current mainnet configuration. This uncertainty should be resolved by reviewing the full call graph of `ensure_match_transaction_info` in `chunk_executor/mod.rs`, `replay_on_archive.rs`, and `aptos_debugger.rs` to confirm whether state-checkpoint-hash verification is enforced by any other layer in those flows.

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
