## Title
Replay-verify path accepts divergent state roots because `TransactionOutput::ensure_match_transaction_info` never checks checkpoint hashes - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the invariant used by offline/replay verification tooling to confirm that a freshly re-executed transaction output matches the authenticated `TransactionInfo` recorded on-chain (i.e., in the transaction accumulator). It compares status, gas used, the write-set hash (`state_change_hash`), and the event root hash, but it deliberately skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the fields that actually commit to the JellyfishMerkle/SMT state root, hot-state root, and native "trading" position-state root. This is called directly by `storage/db-tool/src/replay_on_archive.rs`, the tool used to replay and verify archived history against local re-execution.

### Finding Description
`ensure_match_transaction_info` is defined on `TransactionOutput` and is explicitly documented as leaving a gap: [1](#0-0) 

It checks:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `CryptoHash::hash(self.write_set())` vs `txn_info.state_change_hash()`
- computed event root vs `txn_info.event_root_hash()`

but explicitly does **not** validate `state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`, all of which are part of the same `TransactionInfo` (`TransactionInfoV1`) that is hashed into the transaction accumulator leaf and ultimately into the authenticated ledger root. The in-code comment states this outright: it warns that "replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution," and instructs that checkpoint hashes must be validated "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`."

This function is the sole correctness gate used by the offline replay-verify tool: [2](#0-1) 

`execute_and_verify` re-executes the archived transactions with `AptosVMBlockExecutor`, then calls `ensure_match_transaction_info(version, &expected_txn_infos[idx], Some(&expected_writesets[idx]), Some(&expected_events[idx]))` on each output. If this returns `Ok(())`, the chunk is treated as verified and the loop moves on — there is no independent comparison of the state/hot-state/position-state checkpoint roots anywhere else in that code path.

The write set and event root are already covered by other integrity mechanisms elsewhere (accumulator leaf hash, JMT batch application), but the *comparator specifically used to assert "local re-execution == archived authenticated result"* omits the state-commitment fields. Any code path (bug in the VM, a native "trading" position-state computation bug, or divergence in how hot-state entries are folded into the SMT) that produces the correct write set, correct events, correct gas, and correct status, but computes a different state-checkpoint/hot-state/position-state root, will pass `ensure_match_transaction_info` with a false "match."

### Impact Explanation
This breaks the "Proof And Storage Pivots" invariant that replay paths must not accept a differently-committed ledger state as valid: replay-verify is explicitly the tool relied on to catch state-root divergence bugs (including hard-fork-only divergence) between the authenticated history and local re-execution. If the state/hot-state/position-state commitment logic diverges from the reference implementation for any reason, `replay_on_archive` (and any other caller of `ensure_match_transaction_info`, such as the corresponding call site referenced in `aptos-move/cli/src/commands.rs`) will silently report success instead of flagging the mismatch. This directly matches the required impact class: "Wrong accumulator root, Merkle proof, transaction proof, event proof, or state proof accepted as valid" and "Hard-fork-only divergence during commit, replay, restore, or proof verification" — a genuine state-root divergence would go undetected by the tool whose entire purpose is to detect exactly that.

### Likelihood Explanation
The gap is unconditional in the current code (not gated behind a feature flag) — the comparator simply never inspects the checkpoint-hash fields regardless of configuration. The comment ties urgency to "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`," a feature that is present and wired through `BlockExecutorConfigFromOnchain`/`AptosFeature` and referenced across `execution/executor/src/workflow/do_get_execution_output.rs`, `execution/executor/src/block_executor/mod.rs`, and `storage/aptosdb/src/db/aptosdb_writer.rs`, indicating the new position/native-state root computation is actively being built out. The likelihood of exploitation is not "attacker-triggered" in the classic sense; rather it is a systemic verification blind spot that would mask any future bug (VM, JMT batching, hot-state folding, or the new position-state path) that alters the committed state root while leaving the write set/events/gas/status unchanged — which is exactly the scenario the code comment itself anticipates.

### Recommendation
Extend `ensure_match_transaction_info` to also assert equality of `state_checkpoint_hash()`, `hot_state_checkpoint_hash()` (for `TransactionInfoV1`), and `position_state_checkpoint_hash()` against locally recomputed values whenever those fields are populated in `txn_info`, mirroring the pattern already used for `state_change_hash` and `event_root_hash`. At minimum, gate `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (and any related hot-state root feature) from being enabled until this validation is added, as the existing TODO comment already recommends.

### Proof of Concept
Not independently constructible without a concrete state-root-diverging execution bug to exercise; the vulnerability itself is code-level and self-evidenced: the comparator function's implementation (lines 2139–2204 of `types/src/transaction/mod.rs`) provably omits the checkpoint-hash comparisons, and its only production caller, `replay_on_archive.rs::execute_and_verify` (lines 373–406), relies solely on this function's `Ok(())` result to certify a chunk as correctly replayed. A minimal PoC would be: introduce (or trigger) any local state-computation divergence that leaves write set/events/gas/status identical to the archived record but changes the derived Sparse-Merkle/JMT root; `replay_on_archive` will not flag it because no code path compares `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L373-406)
```rust
        let executed_outputs = executor
            .execute_block(
                &txns_provider,
                &self
                    .arc_db
                    .state_view_at_version(current_version.checked_sub(1))?,
                BlockExecutorConfigFromOnchain::new_no_block_limit(), // TODO(HotState): will need to incorporate some features.
                TransactionSliceMetadata::Chunk {
                    begin: *current_version,
                    end: *current_version + cur_txns.len() as u64,
                },
            )
            .map(BlockOutput::into_transaction_outputs_forced)?;
        assert_eq!(executed_outputs.len(), cur_txns.len());

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
