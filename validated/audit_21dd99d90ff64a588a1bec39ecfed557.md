## Title
`ensure_match_transaction_info` skips checkpoint-hash validation, allowing replay/backup verification to accept a corrupted position/hot state root — ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-response invariant used by replay/backup verification tooling to prove that a locally re-executed transaction's output matches the on-chain committed `TransactionInfo` (i.e. that write set, events, status, and gas match the proven `TransactionInfo`). The function explicitly omits comparison of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, all of which are the checkpoint-root fields carried by `TransactionInfoV1` and bound into the transaction accumulator / ledger info. Consequently, a divergence between locally-computed state (or the new position-native state tree) and the value actually committed on-chain can pass full "successful replay" verification.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` at [1](#0-0)  checks status, gas, write-set hash (`state_change_hash`), and event root hash against the given `TransactionInfo`, but the code contains an explicit acknowledgment that it does **not** validate the state/hot-state checkpoint hashes or the new `position_state_checkpoint_hash`: [2](#0-1) 

This function is the sole state-integrity gate used by `storage/db-tool`'s `replay_on_archive` verifier, which drives mainnet backup/replay verification: it re-executes archived transactions and calls `ensure_match_transaction_info` per-transaction to decide whether the replay "passed" [3](#0-2) . Because the comparator never checks `state_checkpoint_hash` (`TransactionInfo::state_checkpoint_hash()`), a state checkpoint root computed differently than the one actually accepted by consensus/execution — e.g., due to a bug in `DoStateCheckpoint`, in the new "trading-native" position-state Merkle tree logic (`compute_trading_native_state_roots`, referenced in the executor pipeline: [4](#0-3)  and [5](#0-4) ), or an unrelated future storage-schema bug — would be silently accepted by `replay_on_archive` as a valid replay, even though the actual committed Merkle/accumulator root for that version diverges from what local re-execution computed.

This directly matches the "Proof And Storage Pivots" invariant that "Accumulators, Jellyfish Merkle structures, versioned state views, and restore paths must preserve deterministic proof binding," and the state-integrity gate's explicit example: "Hard-fork-only divergence during commit, replay, restore, or proof verification." The bug-class analog to the external report ("checked the wrong / incomplete identity before acting") is that the verifier checks the wrong (incomplete) set of fields — it validates everything **except** the actual state-commitment root, which is the one field that should be checked to catch consensus-vs-replay divergence.

### Impact Explanation
This is a High severity integrity issue: it undermines the primary mechanism (`replay_on_archive`) operators and auditors use to detect state divergence in the presence of a non-deterministic or buggy execution/commit path (particularly relevant to the newly-introduced position/trading-native state tree and hot-state checkpoint hash, which are new, less-hardened code paths). If those subsystems ever produce a wrong root (a bug, non-determinism, or a hard-fork-only divergence), the standard verification tooling would report a false "successful replay," masking a corrupted or forked ledger state precisely in the class of case this tool exists to catch.

### Likelihood Explanation
Medium: the gap is not itself a state-corruption bug, but it removes the detection capability for any future/legacy bug in the checkpoint hash computation (state root, hot-state root, or position-native root). Given the code explicitly calls out that `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (new feature) is in-progress and unfinished with respect to this validator, the likelihood of the checked invariant becoming security-relevant increases as that feature is enabled on mainnet. No privileged access is required to trigger — it's an automatic gap that only manifests when an underlying root-computation bug exists elsewhere in the stack.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash()`, `hot_state_checkpoint_hash()` (when present), and `position_state_checkpoint_hash()` (when present and `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled) between `self`/locally computed values and `txn_info`, failing verification (returning an `Err`) on any mismatch, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled on mainnet, exactly as the inline TODO already recommends.

### Proof of Concept
Conceptual (no exploit code needed — this is a verification-logic gap, not a runtime exploit):
1. Suppose local re-execution of a chunk of transactions during `replay_on_archive::execute_and_verify` computes a `state_checkpoint_hash`/`position_state_checkpoint_hash` that differs from the one embedded in the archived, ledger-info-signed `TransactionInfo` (due to any bug in `DoStateCheckpoint`/position-Merkle logic).
2. `execute_and_verify` calls `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_infos[idx], ...)` [6](#0-5) .
3. Because `ensure_match_transaction_info` only checks status/gas/write-set-hash/event-root-hash [7](#0-6)  and never touches `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, the call returns `Ok(())` despite the state root divergence.
4. `replay_on_archive` therefore reports the chunk as verified successfully, hiding a genuine ledger-state fork/corruption from operators.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L387-413)
```rust
        let compute_trading_native_state_roots =
            output.execution_output.compute_trading_native_state_roots;
        let known_position_state_checkpoints = compute_trading_native_state_roots.then(|| {
            txn_infos
                .iter()
                .map(|t| t.position_state_checkpoint_hash())
                .collect_vec()
        });
        let position_persisted = compute_trading_native_state_roots
            .then(|| ProvablePositionStateSummary::new_persisted(self.db.reader.as_ref()))
            .transpose()?;
        let state_checkpoint_output = DoStateCheckpoint::run()
            .execution_output(&output.execution_output)
            .parent_state_summary(&parent_state_summary)
            .persisted_state_summary(&ProvableStateSummary::new_persisted(
                self.db.reader.as_ref(),
            )?)
            .maybe_known_state_checkpoints(known_state_checkpoints)
            .maybe_known_hot_state_checkpoints(known_hot_state_checkpoints)
            // Parent position summary is chained across chunks by the commit
            // queue (seeded from the pre-committed position tip); the persisted
            // base supplies cold-key proofs. The known-hash check validates the
            // computed root against the committed TransactionInfos.
            .maybe_parent_position_state_summary(parent_position_state_summary.as_ref())
            .maybe_persisted_position_state_summary(position_persisted.as_ref())
            .maybe_known_position_state_checkpoints(known_position_state_checkpoints)
            .build()?;
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L192-234)
```rust
    fn get_state_checkpoint_hashes(
        execution_output: &ExecutionOutput,
        known_state_checkpoints: Option<Vec<Option<HashValue>>>,
        computed_last_checkpoint_hash: HashValue,
        label: &str,
    ) -> Result<Vec<Option<HashValue>>> {
        let _timer = OTHER_TIMERS.timer_with(&[&format!("get_{label}_checkpoint_hashes")]);

        let num_txns = execution_output.to_commit.len();
        let last_checkpoint_index = execution_output
            .to_commit
            .state_update_refs()
            .last_inner_checkpoint_index();

        if let Some(known) = known_state_checkpoints {
            ensure!(
                known.len() == num_txns,
                "Bad number of known {label} hashes. {} vs {}",
                known.len(),
                num_txns,
            );
            if let Some(idx) = last_checkpoint_index {
                ensure!(
                    known[idx] == Some(computed_last_checkpoint_hash),
                    "{label} root hash mismatch with known hashes passed in. {:?} vs {:?}",
                    known[idx],
                    Some(computed_last_checkpoint_hash),
                );
            }
            Ok(known)
        } else {
            if !execution_output.is_block {
                // We should enter this branch only in test.
                execution_output.to_commit.ensure_at_most_one_checkpoint()?;
            }

            let mut out = vec![None; num_txns];
            if let Some(index) = last_checkpoint_index {
                out[index] = Some(computed_last_checkpoint_hash);
            }
            Ok(out)
        }
    }
```
