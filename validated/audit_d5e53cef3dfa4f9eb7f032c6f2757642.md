This is a genuinely documented gap in the codebase — I have enough evidence to report a state-integrity finding rather than fabricating one based purely on the external report analog.

### Title
Replay-verify comparator `ensure_match_transaction_info` silently accepts divergent state/hot-state/position Merkle roots - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole comparator used by the `db-tool replay-on-archive` verification pipeline to confirm that a locally re-executed transaction produced the exact same result as the authenticated on-chain `TransactionInfo`. The function checks status, gas, write-set hash, and event-root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the very fields that commit the Jellyfish Merkle state root (and the newer "trading-native" position-tree root) into the authenticated ledger. This is called out in-line as a known TODO in the code itself. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` validates transaction status, gas used, write-set hash and event-root hash against the trusted `TransactionInfo`, but the comment block preceding its `Ok(())` explicitly documents that it "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`)". [2](#0-1) 

This comparator is the only correctness gate used by `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes historical transactions against the archived state view and calls `ensure_match_transaction_info` per transaction to decide pass/fail for the whole replay-verify run: [3](#0-2) 

Because the state/hot-state/position checkpoint hashes are the fields that actually commit the state Merkle root produced by execution (assembled in `DoLedgerUpdate::assemble_transaction_infos`, which builds `TransactionInfo` with `maybe_state_checkpoint_hash`, `maybe_hot_state_checkpoint_hash`, and `maybe_position_state_checkpoint_hash`), a state-root divergence between the archived/authenticated chain and a re-executed VM run at a checkpoint boundary will not be flagged by replay-verify: [4](#0-3) 

The comment further ties this gap directly to an upcoming feature flag, `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, whose rollout is expected to introduce the position-tree state root into `TransactionInfo` V1 — meaning the tool would remain blind to divergence of that root as well unless the comparator is fixed first.

### Impact Explanation
Replay-verify is the primary post-hoc integrity tool used to detect state-commitment divergence (e.g., after a hard fork, VM/gas-schedule change, or a subtle nondeterminism bug) by re-executing historical transactions and comparing results against the authenticated chain. Because the comparator used by this tool never inspects `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, a state-root divergence at any checkpoint boundary — the exact class of bug replay-verify exists to catch — would go completely undetected, silently reporting "no failed transactions" while the recomputed Merkle/JMT state root differs from the one committed on mainnet. This falls squarely in the "hard-fork-only divergence during ... replay ... or proof verification" and "wrong ... state proof accepted as valid" categories from the state-integrity gate, since the state-checkpoint hash is effectively the authenticated state proof root binding for that version.

### Likelihood Explanation
This is not attacker-triggered in the traditional sense; it is a structural gap in an internal verification tool that is invoked by operators/auditors specifically to catch state divergence bugs (e.g. during hard-fork audits or gas-schedule changes). The likelihood that this blind spot matters is directly tied to the likelihood of any other state-computation divergence bug existing — in which case this comparator would fail to catch it, defeating the tool's purpose at the worst possible time.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash()`, `hot_state_checkpoint_hash()` (if present), and `position_state_checkpoint_hash()` (if present) between the locally recomputed `TransactionInfo` and the expected/authenticated one, consistent with how `do_ledger_update.rs` populates these fields at commit time. This should be done before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, per the existing TODO note in the code.

### Proof of Concept
1. Take an archived range of transactions containing a checkpoint (state-checkpoint or block-epilogue) transaction.
2. Re-execute that range through `replay_on_archive`'s `execute_and_verify`, but with a state view/VM whose Merkle/JMT state root at the checkpoint diverges from the authenticated chain (e.g., simulate a subtle nondeterministic write that changes a state value but preserves gas/status/write-set-hash/event-hash — e.g. an object ordering or timestamp-dependent value written elsewhere that isn't part of the diffed write set/event content, or more directly, patch the comparator's inputs to omit checkpoint hash agreement).
3. Observe that `ensure_match_transaction_info` returns `Ok(())` and the replay-verify run reports zero failed transactions, despite `state_checkpoint_hash` differing from the archived `TransactionInfo`, because the function never compares that field. [1](#0-0)

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L95-121)
```rust
                let txn_info = if transaction_info_v1 {
                    TransactionInfo::builder_v1()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .maybe_hot_state_checkpoint_hash(
                            hot_state_checkpoint_hashes.and_then(|hot| hot[i]),
                        )
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .maybe_position_state_checkpoint_hash(
                            position_state_checkpoint_hashes.and_then(|p| p[i]),
                        )
                        .build()
                } else {
                    TransactionInfo::builder_v0()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .build()
                };
```
