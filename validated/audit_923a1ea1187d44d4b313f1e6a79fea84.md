### Title
Replay-verify tooling (`TransactionOutput::ensure_match_transaction_info`) never validates the state-checkpoint root hash, letting a wrong state root pass verification undetected - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` — the routine used by `aptos-debugger` and `storage/db-tool`'s `replay_on_archive` to confirm that locally re-executed transactions match the transaction infos recorded on-chain — checks transaction status, gas used, the write-set hash (`state_change_hash`), and the event root hash, but never compares `TransactionInfo::state_checkpoint_hash()` (or `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against anything computed from the replay. The code even contains a self-acknowledged TODO admitting the gap.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates four properties of a re-executed `TransactionOutput` against the archived `TransactionInfo`: status, gas used, write-set hash, and event root hash. It stops short of checking any of the checkpoint hash fields carried by `TransactionInfo`: [2](#0-1) 

This comment openly documents that the comparator "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`)", so replay-verify can report success even when the authenticated state root diverges from local execution.

This function is the sole verification gate used by the offline replay-verify tool `storage/db-tool/src/replay_on_archive.rs`, which drives full-history re-execution comparisons used to confirm VM/storage upgrades are safe before shipping: [3](#0-2) 

and by `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs`, both of which call the same function to assert re-execution matches recorded results. None of these callers independently verify the state Merkle root (state checkpoint hash) elsewhere in the same code path.

Because `state_checkpoint_hash` is the field that actually authenticates the post-block/post-checkpoint global state (JMT root), and it is entirely absent from this check, any bug that produces a *different* global state tree root while still producing per-transaction write-sets/events that hash identically (e.g., a bug in state-checkpoint construction, JMT batching, hot-state merge, or `DoStateCheckpoint`/`merklize_main_state` logic that mis-applies deltas but leaves individual write ops intact) would go completely undetected by these tools.

### Impact Explanation
Replay-verify (`db-tool replay-on-archive`) and the debugger's replay-check are the primary tools operators and the Aptos team rely on to confirm that a new VM/storage/executor build reproduces mainnet history bit-for-bit before it is rolled out. Because the state-checkpoint root is never validated, a state-computation regression that alters the committed state tree (while write-set/event hashes still match) would pass replay-verify as "correct", allowing a build with a hard-fork-causing state divergence to reach validators undetected. This falls squarely under "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "wrong accumulator/state root ... accepted as valid" from the state-integrity gate.

### Likelihood Explanation
This is not a hypothetical: it requires only that state-checkpoint construction diverges from write-set replay while individual `WriteOp`s are unchanged — plausible for any bug in the JMT/hot-state summarization pipeline (e.g., `storage/aptosdb/src/state_store/state_snapshot_committer.rs`), which is a fairly complex and actively-evolving area (hot state, position state, trading-native roots). The gap is unconditional and always present regardless of feature flags; the TODO comment itself flags it as an area of concern for upcoming `COMPUTE_TRADING_NATIVE_STATE_ROOTS` work, confirming the authors are aware the checkpoint hash is unauthenticated by this codepath today.

### Recommendation
Extend `ensure_match_transaction_info` to also compare the locally-computed state-checkpoint hash (and hot-state / position-state checkpoint hash when applicable) against `txn_info.state_checkpoint_hash()` whenever the transaction is a checkpoint boundary, failing verification on mismatch just as it does for `state_change_hash` and `event_root_hash`. This requires threading the state-checkpoint output computed by `DoStateCheckpoint` into the replay-verify and debugger call sites so a genuine value is available for comparison at checkpoint transactions.

### Proof of Concept
1. Introduce a hypothetical (or actual future) regression in state-checkpoint materialization (e.g., in `merklize_main_state` at [4](#0-3) ) that mis-orders/mis-filters JMT leaf updates for a shard, corrupting the resulting state root while all individual write ops remain byte-identical.
2. Run `storage/db-tool replay-on-archive` over the affected version range; `Verifier::execute_and_verify` calls `ensure_match_transaction_info` at [5](#0-4) , which only checks status/gas/write-set-hash/event-root — the corrupted state root is never inspected.
3. Verification reports success despite the ledger's committed state root differing from what genuine re-execution would produce, i.e., a "wrong root accepted as valid" outcome that could ship a hard-fork-causing regression into production.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L392-406)
```rust
            if let Err(err) = executed_outputs[idx].ensure_match_transaction_info(
                version,
                &expected_txn_infos[idx],
                Some(&expected_writesets[idx]),
                Some(&expected_events[idx]),
            ) {
                cur_txns.drain(0..idx + 1);
                cur_persisted_aux_info.drain(0..idx + 1);
                expected_txn_infos.drain(0..idx + 1);
                expected_events.drain(0..idx + 1);
                expected_writesets.drain(0..idx + 1);

                return Ok(Some(err));
            }
        }
```

**File:** storage/aptosdb/src/state_store/state_snapshot_committer.rs (L29-66)
```rust
pub(crate) fn merklize_main_state(
    state_db: &StateDb,
    last_snapshot: &mut StateWithSummary,
    SnapshotToCommit {
        snapshot,
        hot_state_updates,
    }: SnapshotToCommit,
) -> StateMerkleCommit {
    let version = snapshot.version().expect("Cannot be empty");
    let base_version = last_snapshot.version();
    let previous_epoch_ending_version = state_db
        .ledger_db
        .metadata_db()
        .get_previous_epoch_ending(version)
        .unwrap()
        .map(|(v, _e)| v);
    let min_version = last_snapshot.next_version();

    // Element format: (key_hash, Option<(value_hash, key)>). Routes
    // through the shared `LeafEntry`-based extractor — same shape
    // position-shaped pipelines use. Main state's per-slot filter
    // (`passes_jmt_filter`, which checks `value_version`/
    // `hot_since_version >= min_version`) skips entries that haven't
    // changed since the last snapshot; position-shaped pipelines'
    // default `passes_jmt_filter` returns `true`.
    let all_updates: Vec<_> = snapshot
        .make_delta(last_snapshot)
        .shards
        .iter()
        .map(|updates| {
            let _timer = OTHER_TIMERS_SECONDS.timer_with(&["hash_jmt_updates"]);
            updates
                .iter()
                .filter(|(_key_hash, slot)| slot.passes_jmt_filter(min_version))
                .map(|(key_hash, slot)| leaf_entry_to_jmt_update(key_hash, &slot))
                .collect::<Vec<_>>()
        })
        .collect();
```
