I found a concrete, code-documented integrity gap. The comparator that the chunk executor uses to validate replayed/restored execution results against the authenticated `TransactionInfo` never checks the state root fields, even though the function's own TODO comment says so.

### Title
`TransactionOutput::ensure_match_transaction_info` skips state-checkpoint-hash comparison, letting replay-verify accept a wrong state root - (File: `types/src/transaction/mod.rs`)

### Summary
`ensure_match_transaction_info` is the single-source-of-truth comparator used by the chunk executor's `verify_execution` path to confirm that locally re-executed transactions produced the same result as the authenticated `TransactionInfo` (backed by validator-signed `LedgerInfo` via the transaction accumulator). It compares status, gas, write-set hash (`state_change_hash`), and event root hash, but deliberately skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the fields that actually commit to the state Merkle root at that version.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  hashes and compares `write_set` against `state_change_hash` and events against `event_root_hash`, but its own inline comment admits: [2](#0-1) 

"this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution."

This function is invoked from `ChunkExecutor::verify_execution` in [3](#0-2) , which re-executes a chunk of transactions against locally computed state and compares each output against the `transaction_infos` taken straight from a backup/replay source. Because `state_checkpoint_hash` (the Sparse-Merkle-Tree root summarizing world state at that version, per its doc comment at [4](#0-3) ) is never compared, a divergence between the locally computed state root and the backup's/authenticated state root goes completely undetected by this verification path.

### Impact Explanation
Replay-verify is the mechanism operators and auditors rely on to detect non-determinism, storage-schema bugs, or hard-fork-only divergence between what a node computes and what was actually committed to the chain. If the state root diverges (e.g., due to a VM/storage bug, a bad hard-fork migration, or corrupted backup data) but every other field (status, gas, write-set hash, event root) happens to match, `verify_execution` reports success while the authenticated state root and the locally recomputed root disagree. This directly matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" impact class: the safety net meant to catch exactly this class of bug is silently blind to the one field (`state_checkpoint_hash`) that proves state correctness.

### Likelihood Explanation
This is not a hypothetical: the gap is explicitly documented in the source as a known, unaddressed TODO, meaning it is a permanent blind spot in every use of `verify_execution`/replay-verify tooling until fixed, not a rare edge case requiring adversarial timing.

### Recommendation
In `ensure_match_transaction_info`, additionally recompute and compare `state_checkpoint_hash` (and `hot_state_checkpoint_hash`/`position_state_checkpoint_hash` when the relevant feature flags are enabled) against the locally computed state root before returning `Ok(())`, so replay-verify/backup-verify fails loudly on any state-root divergence.

### Proof of Concept
No new PoC code is required beyond what the repository already documents: any call path that reaches `ChunkExecutor::verify_execution` (`execution/executor/src/chunk_executor/mod.rs:648-706`) with a chunk of `transaction_infos` whose `state_checkpoint_hash` differs from the true state root — while other fields (status/gas/write-set hash/event root) match — will return `Ok(end_version)` from `verify_execution`, i.e. "verified successfully," despite the state root mismatch, exactly as flagged by the TODO at `types/src/transaction/mod.rs:2197-2203`.

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

**File:** types/src/transaction/mod.rs (L2409-2412)
```rust
    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,
```

**File:** execution/executor/src/chunk_executor/mod.rs (L685-706)
```rust
        for (version, txn_out, txn_info, write_set, events) in multizip((
            begin_version..end_version,
            &execution_output.to_commit.transaction_outputs,
            transaction_infos.iter(),
            write_sets.iter(),
            event_vecs.iter(),
        )) {
            if let Err(err) = txn_out.ensure_match_transaction_info(
                version,
                txn_info,
                Some(write_set),
                Some(events),
            ) {
                return if verify_execution_mode.is_lazy_quit() {
                    error!("(Not quitting right away.) {}", err);
                    verify_execution_mode.mark_seen_error();
                    Ok(version + 1)
                } else {
                    Err(err)
                };
            }
        }
```
