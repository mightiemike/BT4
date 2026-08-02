## Title
`TransactionOutput::ensure_match_transaction_info` skips state-checkpoint hash comparison, allowing replay/debugger verification to accept a diverged state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay-verification and debugger tooling to confirm that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` committed on-chain. It checks status, gas used, write-set hash, and event root hash, but its own inline comment states it intentionally does **not** check `state_checkpoint_hash` (nor `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against the locally computed value. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` compares four fields between the locally produced `TransactionOutput` and the trusted, ledger-committed `TransactionInfo`: execution status, gas used, write-set hash (`state_change_hash`), and event root hash. [2](#0-1) 

It never compares `txn_info.state_checkpoint_hash()` (or the V1-only `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against a state root computed from local re-execution. The function's own TODO comment acknowledges this:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [3](#0-2) 

This function is called from `aptos-move/aptos-debugger/src/aptos_debugger.rs` and referenced by `storage/db-tool/src/replay_on_archive.rs` — both are tools whose entire purpose is to catch divergence between locally re-executed VM output and mainnet's committed `TransactionInfo` (i.e., replay-verify / hard-fork-detection tooling). The `state_checkpoint_hash` field is the accumulator/Merkle root binding a transaction's post-execution state — it is exactly the kind of "wrong accumulator root ... accepted as valid" case called out by the state-integrity gate. By omitting it from the comparison, any code path (native trading state roots, hot-state root, or ordinary state Merkle root reconstruction bugs) that produces a state root divergent from the one committed to the ledger will not be flagged by this check. [4](#0-3) 

### Impact Explanation
If a local execution/state-checkpoint computation diverges from the authenticated `TransactionInfo.state_checkpoint_hash` for any reason (an executor bug, a corrupted state root during a feature rollout, or an intentionally malicious historical fork), `ensure_match_transaction_info` will still report a match as long as status, gas, write-set hash, and events line up. Since this routine backs replay-verify/debugger tooling that is specifically meant to catch exactly this class of divergence, the check silently loses its ability to detect a corrupted or wrong state-commitment root — undermining confidence in replay-verify results used to validate historical ledger integrity and to detect hard forks.

### Likelihood Explanation
This is a self-acknowledged gap (explicit TODO in the code) rather than a hypothetical one, so the underlying condition (checkpoint hash omitted from comparison) is proven by the local code itself. However, the comment gates the concern behind the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature not yet being enabled ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"), and the state-integrity gate in this task explicitly treats native-trading-only issues as potentially out of primary scope. The `state_checkpoint_hash` field, however, is not exclusive to trading-native; it is the general per-checkpoint state root used across ordinary Aptos accounts too, so this gap in principle also affects detection of ordinary state-root divergence during replay verification, not just the trading-native rollout.

### Recommendation
Extend `ensure_match_transaction_info` to compute and compare the locally derived state-checkpoint hash(es) (`state_checkpoint_hash`, and when applicable `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against the values in the trusted `TransactionInfo`, and fail with an error on mismatch, before any feature that relies on these commitments is enabled.

### Proof of Concept
I was not able to fully trace a concrete exploitable trigger (e.g., a specific executor bug that produces a diverging checkpoint hash) within the available indexed code/tool budget — the only proven fact is the code-level gap itself (comparison omission acknowledged by the inline TODO) and its use by replay/debugger tooling designed to catch this exact class of divergence. Given the limited remaining budget, I could not fully verify the call sites in `storage/db-tool/src/replay_on_archive.rs` (file content was not returned by the indexer) or confirm whether other layers (e.g., accumulator root verification during full replay) independently catch the same divergence through a different mechanism. I recommend a Devin session with full repository access to check whether the state Merkle root is independently verified elsewhere in the replay pipeline (which would reduce this to a defense-in-depth gap rather than a primary detection failure).

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
