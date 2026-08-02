### Title
`ensure_match_transaction_info` skips checkpoint-hash validation, letting replay/verify tooling accept a diverged state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verify tooling to confirm that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` recorded on-chain. It checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but the developers' own inline comment documents that it deliberately skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the locally computed values. [1](#0-0) 

### Finding Description
The function validates several fields of `TransactionInfo` against the locally produced `TransactionOutput`:
- status [2](#0-1) 
- gas used [3](#0-2) 
- write-set hash [4](#0-3) 
- event root hash [5](#0-4) 

But it never checks `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` against anything derived from the locally computed state. This gap is explicitly called out in the trailing comment: [6](#0-5) 

This means the only integrity signal this function gives about the *state* produced by re-execution is the per-transaction write-set hash (`state_change_hash`), not the accumulated Sparse-Merkle-Tree root that authenticates the full account/resource state at a checkpoint. Two different `TransactionInfo` state roots (main, hot, or position state) could correspond to executions whose per-transaction write sets hash identically transaction-by-transaction while the accumulated Merkle root diverges (e.g. due to a state-tree construction bug, storage corruption on the base being extended, or a code path that doesn't feed the same writes into the checkpoint SMT as it commits to `TransactionOutput.write_set()`), and this function would report success.

`ensure_match_transaction_info` is used by `storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs`.

### Impact Explanation
The affected callers are replay/verify tooling used to validate that historical or bootstrapped ledger state matches consensus-committed `TransactionInfo`. If a bug in state-checkpoint-hash computation (or a storage corruption during restore/replay) diverges from the ledger's authenticated `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, this integrity check silently passes because it never inspects those fields. This directly maps to the "authenticated API/state-view output bound to the wrong ... proof context" and "wrong ... state proof accepted as valid" impact class: a divergent state root can be reported as a verified match by the tooling that is specifically meant to catch such divergence.

The severity is bounded by the fact that:
1. `ensure_match_transaction_info` does not itself gate consensus-level state commitment — it is a downstream, offline verification/replay tool, not part of the executor's commit path that determines what is written to the DB.
2. The gap is explicitly gated by the not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag path (per the comment), suggesting the position/native-trading state root is the primary motivating case, and the TODO itself frames this as pending future work rather than a currently-exploitable path for the already-shipped `state_checkpoint_hash`/`hot_state_checkpoint_hash` fields.

### Likelihood Explanation
Low-to-moderate. Exploiting this requires an underlying divergence between the checkpoint-hash actually computed by re-execution and the one recorded on-chain — this function doesn't create the divergence itself, it only fails to detect one that already exists. Given `state_change_hash` (the write-set hash) is checked, most naturally-occurring corruption of the write set is caught; a divergence that specifically affects only the accumulated Merkle-tree root without changing any individual write's serialized bytes is a narrower, harder-to-trigger scenario (e.g., a bug in `State::update`/hot-state promotion bookkeeping, or SMT batch-update logic, that is orthogonal to the write op itself).

### Recommendation
Extend `ensure_match_transaction_info` to also compare a locally computed `state_checkpoint_hash` (and, where applicable, `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`) against the corresponding fields on `txn_info`, as the existing TODO already recommends, rather than deferring this until `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled. At minimum, replay/verify tools that rely on this function (`replay_on_archive`, `aptos-debugger`, `cli/commands.rs`) should independently assert the checkpoint hash whenever it is available in the locally produced execution output.

### Proof of Concept
Not independently reproducible from static analysis alone — the vulnerability is a documented gap in verification coverage rather than a demonstrable code path that produces incorrect state today. Confirming real-world exploitability would require constructing an execution scenario where the write-set hash for every transaction in a chunk matches while the derived state/hot-state/position-state checkpoint root diverges (e.g., via a hot-state promotion/eviction bookkeeping bug), and showing that `replay_on_archive` or `aptos-debugger` reports success despite that divergence. This is left as unverified given the constraints of this review; the finding rests on the confirmed absence of the check itself, which is acknowledged by the code's own TODO comment.

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
