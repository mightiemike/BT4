## Finding: `ensure_match_transaction_info` skips checkpoint-hash verification during chunk replay verification

### Title
Replay/backup verification accepts execution with a divergent state-checkpoint root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by `ChunkExecutorInner::verify_execution` (state-sync chunk replay / `--verify-execution-mode`) to confirm that locally re-executed transaction outputs match the `TransactionInfo`s received from a peer/backup before they are trusted and committed. This function checks transaction hash, status, gas used, write-set hash, and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`.

### Finding Description [1](#0-0) 

The function's own comment documents the gap: [2](#0-1) 

It states: "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is invoked from `ChunkExecutorInner::verify_execution`, which is the code path used to verify a chunk of transactions replayed from a backup/state-sync source before accepting its `TransactionInfo`s (and thus the associated state-checkpoint hash) as ground truth going forward: [3](#0-2) 

Because `state_checkpoint_hash` is skipped, if locally re-executed state diverges from the state root claimed in the untrusted `TransactionInfo` (e.g., due to a state-computation bug, or a subtly corrupted backup/restore source), `verify_execution` will report success anyway. The mismatched checkpoint hash is never independently recomputed and compared here — verification only cross-checks write set, events, gas, and status, all of which can be correct while the JMT/state root is wrong.

### Impact Explanation
If this verification path is relied upon to validate that a replayed/restored chunk's claimed state root actually matches locally computed state (this is its documented purpose per the TODO), a state-root divergence would go undetected. That means a node performing chunk replay/verify-execution could accept and persist a `TransactionInfo` whose `state_checkpoint_hash` does not correspond to the actual computed ledger state, while still reporting the replay as verified/successful. This is exactly the "wrong accumulator root/state proof accepted as valid" and "replay path... must not reinterpret committed data into a different ledger state" class of impact called out by the state-integrity gate, since a corrupted or diverged state root can pass replay verification undetected.

### Likelihood Explanation
This is a real, currently-existing gap acknowledged by an inline TODO comment in the code itself (referencing an unmerged/disabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature), meaning the developers are aware of it. Its practical trigger requires an independent bug or corruption elsewhere that produces a wrong state-checkpoint hash during chunk replay — this function does not itself corrupt state, it fails to *detect* corruption. Because the primary consensus commit path (`DoLedgerUpdate::assemble_transaction_infos`) computes `state_checkpoint_hash` independently from local execution rather than trusting an untrusted `TransactionInfo`, on the normal block-execution path this gap does not directly enable a wrong root to be committed. The exposure is specifically confined to the chunk-replay/verify-execution and backup/restore verification tooling path, which is a genuine but narrower "authenticated response/proof context" concern (verifying an externally-provided, not locally-derived, `TransactionInfo`).

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally computed state-checkpoint hashes (as already computed by `DoStateCheckpoint`) whenever chunk replay/verify-execution performs the comparison, closing the gap the TODO already flags before it is relied upon in any critical restore/replay path.

### Proof of Concept
No dynamic PoC is available from static review alone; the flaw is demonstrated directly by the function body and its self-documenting TODO: [1](#0-0) 
combined with its caller in the chunk-replay verification path that never separately validates the checkpoint hash: [4](#0-3) 

**Caveat:** I could not fully trace every downstream consumer of `verify_execution`'s result (e.g., whether backup/restore tooling treats "verified" success as authorization to persist the state root without any other independent check), so I cannot confirm end-to-end that this alone allows a corrupted root to reach a mainnet node's durable ledger without any other safeguard. Given the inherent uncertainty about whether an additional independent state-root check exists elsewhere in the restore/verify pipeline, this should be treated as a real but scoped gap in defense-in-depth for replay/backup verification rather than a confirmed direct commit-integrity break.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2203)
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
