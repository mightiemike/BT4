## Title
`ensure_match_transaction_info` skips checkpoint-hash comparison, allowing replay-verify to accept a corrupted state/hot-state/position-state root as valid - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single authenticated-equivalence check used by both `ChunkExecutor::verify_execution` (state-sync/backup "verify" pipelines) and the `db-tool replay-on-archive` verifier to confirm that locally re-executing a chunk against a downloaded/archived `TransactionInfo` list reproduces the exact same committed result. The function checks status, gas, write-set hash, and event root hash, but — per its own inline TODO — deliberately omits comparison of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`.

### Finding Description [1](#0-0) 

The comment embedded in the function is explicit about the gap: [2](#0-1) 
"this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is called from three consumers that are all meant to assert cryptographic equivalence between local execution and an externally supplied, ledger-committed `TransactionInfo`:
- `execution/executor/src/chunk_executor/mod.rs::verify_execution`, used by state-sync/backup verify flows to confirm a locally re-executed chunk matches trusted `transaction_infos`: [3](#0-2) 
- `storage/db-tool/src/replay_on_archive.rs::execute_and_verify`, the tool operators run to verify an archived/restored DB replays correctly against expected `TransactionInfo`s: [4](#0-3) 
- `aptos-move/aptos-debugger/src/aptos_debugger.rs::print_mismatches`.

Because `state_checkpoint_hash` (main state Merkle root), `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (native-position Merkle root, added by this fork's trading feature — see `TransactionInfoV1` fields at [5](#0-4) ) are never compared, a chunk whose write set, events, gas, and status match but whose actual resulting state root (main, hot, or native-position) diverges from the `TransactionInfo` being verified against will still be reported as "verified" successfully.

### Impact Explanation
This affects the State-Integrity Gate's core invariant: authenticated proof-bearing output must stay bound to the correct ledger root, and replay/restore verification tooling must not accept a divergent committed state as valid. If a bug in state-checkpoint computation (main state, hot state, or the fork's native-position JMT) silently corrupts the resulting root while write set/events/gas/status remain unaffected — e.g. a bug in `DoStateCheckpoint`, in `position_summary_at_commit` (`storage/aptosdb/src/db/aptosdb_writer.rs:406-477`), or in the position JMT merklize path (`storage/aptosdb/src/position_snapshot_committer.rs`) — the replay-verify and backup-verify tools that exist specifically to catch such divergence will pass silently. This undermines the integrity guarantee these tools are relied upon to provide for detecting hard-fork-only divergence or storage corruption during restore/replay, which falls squarely within the requested "Proof And Storage Pivots" scope (restore paths, JMT-backed structures must preserve deterministic proof binding).

However, this is a detection/verification gap, not itself a mechanism that corrupts committed state on mainnet: normal block commit does not call `ensure_match_transaction_info` to gate what gets written — `TransactionInfo` (including checkpoint hashes) is assembled directly from local execution results in `DoLedgerUpdate::run`/`assemble_transaction_infos` [6](#0-5) , and the transaction accumulator is built over those hashes, so an honest node's own commit path is unaffected. The impact is limited to masking bugs from the specific tools designed to independently confirm root correctness (replay-verify, db-tool verify, aptos-debugger mismatch printer) — it is a blind spot in an integrity *check*, not a path that itself produces or accepts a wrong root during normal consensus/execution/commit.

### Likelihood Explanation
The condition triggers whenever these verify tools are run against a chunk with a state-root bug that doesn't also alter write set/events/gas/status — plausible for logic bugs isolated to checkpoint/root computation (e.g., in the position-state summary/merklize code paths this fork adds). No malicious actor input is required; it's a code-level gap that silently degrades verification tooling, and the code's own TODO confirms it is a known, currently-unaddressed condition ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`").

### Recommendation
Extend `ensure_match_transaction_info` to compare `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()` against the locally computed equivalents (threading them in similarly to `expected_write_set`/`expected_events`) before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, so that `verify_execution`, `replay_on_archive`, and the debugger mismatch tooling all fail loudly on any state-root divergence rather than silently passing.

### Proof of Concept
Not applicable as an exploitable transaction — this is a gap in offline verification tooling, not a state-transition bug reachable via a transaction. Confirmation is via code inspection: `ensure_match_transaction_info` at [7](#0-6)  never reads `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` from `txn_info`, while `TransactionInfoV1` carries all three fields [5](#0-4) , and its only checkers (`verify_execution`, `replay_on_archive::execute_and_verify`, `aptos_debugger::print_mismatches`) rely solely on this function to detect divergence.

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

**File:** types/src/transaction/mod.rs (L2261-2284)
```rust
    #[builder(finish_fn = build)]
    pub fn builder_v1(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        hot_state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
        position_state_checkpoint_hash: Option<HashValue>,
    ) -> Self {
        Self::V1(TransactionInfoV1::new(
            transaction_hash,
            state_change_hash,
            event_root_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            gas_used,
            status,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
        ))
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

**File:** storage/db-tool/src/replay_on_archive.rs (L388-406)
```rust
        for idx in 0..cur_txns.len() {
            let version = *current_version;
            *current_version += 1;

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L30-45)
```rust
        // Assemble `TransactionInfo`s. The variant (V0 vs V1) is driven by the
        // `TRANSACTION_INFO_V1` on-chain feature, threaded via
        // `ExecutionOutput::transaction_info_v1`. The hot state root hash a V1 carries is
        // present only when `HOT_STATE_ROOT_IN_TXN_INFO` is also on (`DoStateCheckpoint`
        // produces `Some` hashes iff so); otherwise the V1 leaves it `None`.
        let (transaction_infos, transaction_info_hashes) = Self::assemble_transaction_infos(
            &execution_output.to_commit,
            execution_output.transaction_info_v1,
            &state_checkpoint_output.state_checkpoint_hashes,
            state_checkpoint_output
                .hot_state_checkpoint_hashes
                .as_deref(),
            state_checkpoint_output
                .position_state_checkpoint_hashes
                .as_deref(),
        );
```
