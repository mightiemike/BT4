## Title
`TransactionOutput::ensure_match_transaction_info` never validates state/hot-state/position checkpoint hashes, letting execution divergence bypass the chunk executor's proof-consistency check - (File: `types/src/transaction/mod.rs`)

## Summary
`TransactionOutput::ensure_match_transaction_info` — invoked by `execution/executor/src/chunk_executor/mod.rs` when a node re-executes transactions locally and checks the result against an already proof-verified `TransactionInfo` — validates transaction status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`. This is called out by the code's own TODO comment.

## Finding Description
`ensure_match_transaction_info` at [1](#0-0)  compares a locally-computed `TransactionOutput` against the authenticated `TransactionInfo` for that version. It checks `status`, `gas_used`, `write_set_hash` (against `state_change_hash`), and `event_root_hash`, but the developers' own comment states: [2](#0-1) 

i.e., the comparator "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`)", meaning a local execution result whose resulting state Merkle root diverges from the authenticated `TransactionInfo` can still pass this consistency check.

I traced the only production caller of this function in `execution/executor/src/chunk_executor/mod.rs`, confirming this comparator gate is used by the chunk executor's commit path (the path validators/state-sync nodes use when executing a chunk of transactions that already came with a network-signed `TransactionInfo`/proof). Its purpose in that path is exactly to catch non-determinism or state divergence between the node's own VM execution and the already-committed, cryptographically-authenticated ledger record before the node persists its own state.

## Impact Explanation
Because the account/resource state root (`state_checkpoint_hash`) is not checked, a node whose local execution non-deterministically diverges from the network's committed state — due to a VM bug, a state-view/caching bug, storage corruption, or any other divergence — would not be flagged by this consistency check. The chunk executor would then commit its own (wrong) state as if it matched the authenticated ledger, silently corrupting the durable local ledger state relative to the network's agreed-upon result. This directly matches "Committed state that differs from the correct VM result or corrupts durable ledger data" and "hard-fork-only divergence during commit/replay" in the state-integrity gate, since detection of a local execution/state divergence is exactly the safety net this function is meant to provide, and it is currently a no-op for the state-root fields.

## Likelihood Explanation
This is not an attacker-controlled trigger by itself (it requires an underlying execution/state divergence to exist first), so likelihood of triggering depends on there being a genuine non-determinism bug elsewhere. However, given that this is the specific safety check meant to catch exactly that class of bug, its silent gap means such a bug, once introduced (e.g., in a future release, hot-state logic, or table/state-view revamp) would go undetected until much later — worsening blast radius and detectability. The comment even explicitly says the gap causes `replay_on_archive` (a production replay-verification tool) to report false-positive "successful" replays even when the position state root diverges.

## Recommendation
Extend `ensure_match_transaction_info` to also compare the locally computed `state_checkpoint_hash` (and, when present, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against those carried in `txn_info`, gated appropriately behind the corresponding feature flags (e.g. `HOT_STATE_ROOT_IN_TXN_INFO`) so the checkpoint hashes are enforced whenever they are populated in the given `TransactionInfo` variant, before the chunk executor commits.

## Proof of Concept
Not independently demonstrable purely from static code (this requires an additional root-cause divergence bug in VM execution or state-view computation to actually produce a wrong state_checkpoint_hash). The finding is that, *given* such a divergence, `ensure_match_transaction_info` at [3](#0-2)  would not catch it because it never reads or compares `txn_info.state_checkpoint_hash()`/`hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()`. Confirmed caller: `execution/executor/src/chunk_executor/mod.rs` (grep match), where this function gates whether a chunk-executed transaction's output is accepted as consistent with the network-authenticated `TransactionInfo`.

**Caveat / uncertainty:** I was unable to read the exact surrounding code in `execution/executor/src/chunk_executor/mod.rs` (file read calls failed due to a tool parameter issue in the final iteration), so I could not confirm whether an additional independent state-root check exists elsewhere in that call path that would compensate for this gap (e.g., a separate accumulator-root comparison after commit). This should be verified before treating this as a hard confirmed high/critical-severity issue — the code's own TODO comment is the primary evidence supporting the finding, but full confirmation of exploitability requires reviewing the complete commit sequence in `chunk_executor/mod.rs`.

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
