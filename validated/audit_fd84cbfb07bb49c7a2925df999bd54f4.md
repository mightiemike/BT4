## Title
`ensure_match_transaction_info` skips checkpoint-hash verification during replay, letting a divergent state/hot-state/position root pass as "verified" - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay-verification tooling (chunk executor replay and `db-tool replay-on-archive`) to confirm that a locally re-executed transaction output matches the authenticated `TransactionInfo` stored on-chain/in the accumulator. The function checks status, gas used, write-set hash, and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents as a `TODO`.

### Finding Description [1](#0-0) 

The comparator explicitly states:
> "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is called from `chunk_executor/mod.rs` and `db-tool/src/replay_on_archive.rs` — both replay-verification code paths whose entire purpose is to detect divergence between locally computed execution results and the authenticated ledger data pulled from a backup/archive/peer. Because the comparator omits checkpoint-hash fields, if the locally computed state tree (state Merkle root, hot-state root, or the newer position/trading-native state root) diverges from the authenticated `TransactionInfo.state_checkpoint_hash` (etc.) due to any bug in state-tree construction, replay path handling, or a version-skew between different `TransactionInfo` variants (V0 vs V1, with/without hot-state root, with/without `position_state_checkpoint_hash`), replay-verify will still report success.

### Impact Explanation
Replay-verification is a state-commitment integrity control: it is meant to catch exactly the class of bug this report covers — "committed state that differs from the correct VM result." Since `state_checkpoint_hash` (present since V0) is excluded from this check unconditionally, replay-verify tooling structurally cannot detect a state-root divergence via this path, undermining a documented invariant that "VM outputs, transaction infos ... must survive executor-to-storage handoff unchanged" and that "replay paths ... must not reinterpret committed data into a different ledger state" without detection.

### Likelihood Explanation
This is a real, currently-shipped gap acknowledged by an in-code `TODO` comment authored by the developers themselves rather than a hypothetical I am inferring. However, I could not fully verify within this investigation whether `state_checkpoint_hash` divergence is independently caught somewhere else in the replay/chunk-executor pipeline (e.g., via `DoStateCheckpoint::get_state_checkpoint_hashes`, which does compare `known_state_checkpoints` against computed hashes when a `known_state_checkpoints` list is supplied). If that separate path is always exercised alongside `ensure_match_transaction_info` on every replay-verify invocation, the practical exploitability of the state_checkpoint_hash gap could be lower than for hot-state/position roots, which appear to have no such independent check identified in the code I found. I was not able to trace the full call graph of `chunk_executor/mod.rs`'s use of this function against `DoStateCheckpoint` to confirm whether that safety net always applies during a replay-verify run within the remaining investigation budget.

### Recommendation
Extend `ensure_match_transaction_info` to also validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` whenever the corresponding fields are present (`Some`) on the `TransactionInfo`, and require the caller to supply the locally-computed checkpoint hashes for comparison exactly as is done for `write_set_hash` and `event_root_hash`, closing the gap described in the existing TODO before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled more broadly.

### Proof of Concept
Not independently constructed as an executable PoC within this investigation; the finding is grounded directly in the shipped code and its own acknowledging comment at [2](#0-1) , plus confirmed call sites in `execution/executor/src/chunk_executor/mod.rs` and `storage/db-tool/src/replay_on_archive.rs`. A full PoC would require constructing a `TransactionOutput`/`TransactionInfo` pair with matching write-set/event hashes but a deliberately mismatched `state_checkpoint_hash` and confirming `ensure_match_transaction_info` returns `Ok`, which I was not able to execute in this read-only environment.

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
