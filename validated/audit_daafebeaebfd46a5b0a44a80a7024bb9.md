### Title
`TransactionOutput::ensure_match_transaction_info` skips checkpoint-hash validation, allowing execution replay/verify to accept a diverging state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity check used to confirm that a (re-)computed `TransactionOutput` matches the authenticated `TransactionInfo` stored/committed on-chain. It validates status, gas used, write-set hash (`state_change_hash`), and event root hash, but by its own documented admission it does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that authenticate the actual state (Merkle/JMT) root produced by execution.

### Finding Description [1](#0-0) 

```rust
pub fn ensure_match_transaction_info(
    &self,
    version: Version,
    txn_info: &TransactionInfo,
    expected_write_set: Option<&WriteSet>,
    expected_events: Option<&[ContractEvent]>,
) -> Result<()> {
    ...
    // checks status, gas_used, write_set_hash vs state_change_hash, event_root_hash
    ...
    // TODO(trading-native): this comparator ignores the checkpoint hashes
    // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
    // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
    // replay even when the authenticated position state root diverges from
    // local execution. Validate the checkpoint hashes here before enabling
    // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
    Ok(())
}
```

The comment is self-documenting: the function deliberately omits comparing `state_checkpoint_hash` (the root of the Sparse Merkle / Jellyfish Merkle Tree describing world state at end of a checkpoint transaction) and `position_state_checkpoint_hash` against the values recomputed from local execution. `TransactionInfo` explicitly carries these fields for this exact purpose — [2](#0-1)  — yet the verification routine never checks them here.

This function is invoked from at least three call sites that treat a successful `Ok(())` result as proof that locally re-executed output is fully consistent with the committed, accumulator-proof-authenticated `TransactionInfo`:
- `execution/executor/src/chunk_executor/mod.rs` — used during chunk-replay verification when applying/verifying transaction chunks from state sync or backup restore.
- `storage/db-tool/src/replay_on_archive.rs` — the dedicated replay-verify tool whose entire purpose is to detect execution/state divergence against archived history.
- `aptos-move/cli/src/commands.rs`.

Because state-checkpoint/JMT root fields are skipped, none of these call sites can detect a case where the write set and events replay identically (so `state_change_hash`/`event_root_hash` match) but the resulting state tree root (state_checkpoint_hash / hot_state_checkpoint_hash / position_state_checkpoint_hash) computed locally differs from the one embedded in the accumulator-proven `TransactionInfo`.

### Impact Explanation
This breaks the "committed state that differs from the correct VM result... accepted without detection" integrity invariant called out in the task's Proof And Storage Pivots. A verification/replay path (`replay_on_archive`, chunk-executor replay verification during state-sync chunk application) that is specifically relied upon to catch execution or storage divergence will report success even though the authenticated position/state root diverges from what local execution computed. This can mask storage corruption, execution non-determinism, or bugs in the (still gated) "trading-native" state root feature, letting a wrong state root pass as verified in exactly the class of authenticated, proof-bound checks the audit brief targets.

### Likelihood Explanation
The gap is not hypothetical: the TODO explicitly states the intended trigger — enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (an on-chain feature flag referenced in `types/src/on_chain_config/aptos_features.rs`, `storage/aptosdb/src/db/aptosdb_reader.rs`/`aptosdb_writer.rs`) without first fixing this comparator. Because the missing checks live in a widely-shared verification routine (`TransactionOutput::ensure_match_transaction_info`) called from the chunk executor and the replay-verify CLI tool, any code path that turns on state/hot-state checkpoint hash computation for that feature immediately inherits an unprivileged, silent integrity gap — no attacker action beyond triggering a state divergence (e.g., a storage bug, non-determinism, or malicious full-node during restore/backup replay) is needed for the check to falsely pass.

### Recommendation
Extend `ensure_match_transaction_info` to compare the locally computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in `txn_info`) against values recomputed from local execution output before returning `Ok(())`, exactly as already done for `state_change_hash` and `event_root_hash`. This should be done regardless of whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, since the checkpoint hash fields already exist in `TransactionInfoV0`/`V1` today.

### Proof of Concept
1. During chunk replay (state sync bootstrapping, backup restore, or `db-tool replay-on-archive`), craft or induce a scenario where re-execution produces the same write set/events (so `state_change_hash` and `event_root_hash` match) but a different final state tree root than what is recorded on-chain — e.g. via a storage bug or divergent hot-state computation.
2. Call `TransactionOutput::ensure_match_transaction_info` with this locally computed output against the archived `TransactionInfo` (as done in `execution/executor/src/chunk_executor/mod.rs` and `storage/db-tool/src/replay_on_archive.rs`).
3. Observe the function returns `Ok(())` because it never inspects `txn_info.state_checkpoint_hash()`/`hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()`, i.e. the replay/verify tooling reports success despite the state root divergence, exactly as the code's own TODO predicts.

Note: I could not fully trace how/whether `position_state_checkpoint_hash` and `hot_state_checkpoint_hash` are currently populated end-to-end or whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is already enabled on any live network at the time of this scan; that determination would require checking the current on-chain feature-flag status and full call graph of the chunk executor, which is beyond what could be confirmed from the indexed code alone.

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

**File:** types/src/transaction/mod.rs (L2405-2416)
```rust
    /// The hash value summarizing all changes caused to the world state by this transaction.
    /// i.e. hash of the output write set.
    state_change_hash: HashValue,

    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,

    /// The hash value summarizing PersistedAuxiliaryInfo.
    auxiliary_info_hash: Option<HashValue>,
}
```
