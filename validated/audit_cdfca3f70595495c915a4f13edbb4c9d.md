Based on my investigation, I found a genuine, self-documented integrity gap, but I could not fully verify it rises to a mainnet-committed-state divergence rather than a replay-verification/tooling gap. I'll report the strongest candidate along with its caveats.

### Title
`TransactionOutput::ensure_match_transaction_info` skips checkpoint-hash verification, allowing state-root divergence to pass replay/chunk verification - (File: `types/src/transaction/mod.rs`)

### Summary
`ensure_match_transaction_info`, the function that checks a locally re-executed `TransactionOutput` against the authoritative, proof-carrying `TransactionInfo`, only compares status, gas used, write-set hash (`state_change_hash`), and event root hash. It explicitly and intentionally omits comparison of the state checkpoint hash (and hot-state / position-state checkpoint hashes), which is the field that actually commits to the resulting Merkle state root.

### Finding Description [1](#0-0) 

The function verifies:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set_hash` vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

but never touches `txn_info.state_checkpoint_hash()` (nor the newer `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` fields introduced for `TransactionInfoV1`). The comment at lines 2197-2202 explicitly documents this as a known gap: *"this comparator ignores the checkpoint hashes ..., so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."*

This routine is invoked from `execution/executor/src/chunk_executor/mod.rs::verify_execution` (chunk replay/backup verification path) and from CLI/debugger replay tools (`aptos-move/cli/src/commands.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`) — i.e., wherever a chunk of transactions + `TransactionInfo`s + write sets is re-executed and checked for consistency with an already-committed/backed-up ledger. [2](#0-1) 

### Impact Explanation
Because the state-checkpoint hash (the accumulator leaf field that actually commits to the JMT/state root after applying the write set) is never checked, a chunk of transactions whose write-set bytes hash correctly but whose *effective state* diverges from the originally committed state (e.g., due to a state-store bug, incorrect delayed-field materialization, or corrupted restore data that still produces byte-identical write ops but is applied to a different base state) would pass `verify_execution`/replay-verify silently. This defeats the purpose of chunk/backup replay verification as an integrity check on the Merkle state root, and could let a node accept and continue building on top of ledger data whose state root does not match what an honest re-execution would produce — without any error being raised by the intended safety net.

### Likelihood Explanation
This is a real, provable local code gap (the comment even flags it as a to-do), but the practical blast radius is constrained: this check is exercised by chunk-executor replay/backup-verification and debugging/CLI tools, not by the primary consensus commit path (`do_ledger_update.rs`/`do_state_checkpoint.rs`), which independently computes and stores the real `state_checkpoint_hash` from actual state. So this does not by itself let a byzantine validator get an invalid state root accepted by honest validators during normal consensus; it weakens the *secondary* verification tooling (replay-verify, chunk replayer, debugger) used for auditing/db-tool-based integrity checks and possibly some state-sync/replay flows. I could not fully trace, within the available tool budget, whether any of the callers of `verify_execution`/`ensure_match_transaction_info` are reachable from state-sync of *untrusted* peer data on mainnet full nodes (which would elevate this from "test/tooling gap" to a state-integrity issue with mainnet impact) — that would need further tracing of `ChunkExecutorTrait`/`TransactionReplayer` callers in state-sync-driver.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash` (and, when present, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) between the locally computed value and `txn_info`, as the surrounding comment already recommends, before any feature depending on `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled.

### Proof of Concept
Not applicable — no executable PoC was constructed; the finding is based on direct code inspection of the comparator function and its explicit self-documented gap at `types/src/transaction/mod.rs:2197-2202`.

**Caveat:** I was not able to fully confirm, within the available search budget, that this verification path is reachable from untrusted/attacker-controlled data on a mainnet-critical path (as opposed to only being exercised by trusted operator-run replay/backup tooling). If the user needs a definitive mainnet-exploitability determination, a Devin session with full repo access should trace all callers of `ChunkExecutorTrait::verify_execution` / `TransactionReplayer` and `StateSyncChunkVerifier` in `state-sync-driver` to confirm whether peer-supplied chunks reach this code path before being trusted.

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
