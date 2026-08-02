## Title
Chunk-executor's transaction/output validation silently skips state-checkpoint-hash verification (state root divergence not caught) - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the check used by chunk-executor replay/state-sync/backup-verify paths to validate that a locally-produced (or replayed) `TransactionOutput` is consistent with an authenticated `TransactionInfo` (the leaf committed into, and proven by, the transaction accumulator). The function validates status, gas, write-set hash, and event-root hash, but it explicitly and admittedly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` computes `write_set_hash` and `event_root_hash` from the actual `TransactionOutput` and compares them against the corresponding fields carried in the (accumulator-proven) `TransactionInfo`: [2](#0-1) 

However, the function's own comment discloses that the state root fields are intentionally left unchecked: [3](#0-2) 

`TransactionInfoV1` (and V0) carry `state_checkpoint_hash` (Jellyfish Merkle root of the world state at a checkpoint), `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`: [4](#0-3) 

These are precisely the fields that anchor the state (Jellyfish Merkle) root to a given version/ledger-info. Since `ensure_match_transaction_info` is the generic sanity-check used by consumers such as `execution/executor/src/chunk_executor/mod.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs` to confirm that a computed/replayed output matches an already-authenticated `TransactionInfo`, none of these callers get any protection from this function against a state root that diverges from the correct VM result — only the write set (raw diffs) and events are checked, not the resulting state root commitment.

### Impact Explanation
The state-checkpoint hash is the field that ultimately gets embedded (via the transaction accumulator) into the `LedgerInfo` that validators sign and clients/light-clients trust. If any code path relies on `ensure_match_transaction_info` as its *sole* correctness check when accepting a `TransactionOutput`/write-set produced through a route other than full independent state-Merkle recomputation (e.g., chunk-executor replay of `TransactionOutputListWithProof` during fast/output-based state sync, or db-tool `replay-on-archive`/backup verification), a wrong state root would not be flagged by this guard. This matches the "state-commitment differs from correct VM result" and "hard-fork-only divergence during commit/replay/restore" impact categories, because the divergence would only become visible on subsequent full-tree recomputation or hash mismatch elsewhere, not at this checkpoint. Whether this reaches full "critical" severity depends on whether an *independent* state-root check exists downstream in every one of `ensure_match_transaction_info`'s callers — I could not fully trace this in the chunk-executor's commit path (`execution/executor/src/workflow/do_state_checkpoint.rs`, `do_ledger_update.rs`) before running out of iterations, so I cannot confirm this is the *only* line of defense.

### Likelihood Explanation
This is a self-documented gap in the code (the TODO explicitly states that db-tool's `replay_on_archive` "can report a successful replay even when the authenticated position state root diverges from local execution"), suggesting the underlying condition is real and already observed by the Aptos team, but is being deliberately deferred pending the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature. Because it is a known/accepted TODO rather than an independently-proven, currently-exploitable break in an unprivileged path, I cannot assert with confidence that it constitutes an unmitigated, high/critical severity bug on mainnet today — it may well be that state-checkpoint hashes are independently and redundantly verified elsewhere in every caller's pipeline (e.g., against the JMT root recomputed from the applied write set), in which case this gap has no live impact.

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` whenever they are present, by recomputing the relevant Merkle/state roots from the applied write set and current state view, and confirm that every caller of this function (chunk-executor, debugger, replay-verify tooling, backup verification) does not rely on it as the sole state-integrity gate. This should be resolved before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any feature relying on `position_state_checkpoint_hash`) is enabled on mainnet.

### Proof of Concept
Not independently reproducible from local static analysis alone: I could not confirm within the available iterations whether `execution/executor/src/chunk_executor/mod.rs` (the concrete caller in the commit path) performs a redundant, independent state-root check after calling `ensure_match_transaction_info`, or whether it relies on this function exclusively. Without that confirmation, I cannot assert a concrete, self-contained exploit chain purely from this repository's code — the finding is a genuine and cited code-level gap (openly flagged by the authors as a TODO) but its exploitability depends on call-site context I was unable to fully trace before this session ended.

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

**File:** types/src/transaction/mod.rs (L2440-2461)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct TransactionInfoV1 {
    gas_used: u64,
    status: ExecutionStatus,
    transaction_hash: HashValue,
    event_root_hash: HashValue,
    state_change_hash: HashValue,
    state_checkpoint_hash: Option<HashValue>,
    hot_state_checkpoint_hash: Option<HashValue>,
    auxiliary_info_hash: Option<HashValue>,

    /// Repurposed reserved field; `None` matches the prior BCS encoding.
    position_state_checkpoint_hash: Option<HashValue>,
    placeholder1: Option<HashValue>,
    placeholder2: Option<HashValue>,
    placeholder3: Option<HashValue>,
    placeholder4: Option<HashValue>,
    placeholder5: Option<HashValue>,
    placeholder6: Option<HashValue>,
    placeholder7: Option<HashValue>,
}
```
