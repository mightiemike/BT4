## Title
`TransactionOutput::ensure_match_transaction_info` skips state-checkpoint/hot-state/position-checkpoint hash validation, allowing replay-verify to accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verify tooling to check that a recomputed `TransactionOutput` matches the authenticated `TransactionInfo` stored in the ledger accumulator. It validates status, gas used, the write-set hash, and the event root hash, but it explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` carried in `TransactionInfo` (`V0`/`V1`). [1](#0-0) 

### Finding Description
The function's own code comment documents the gap:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
Ok(())
``` [2](#0-1) 

The checks that are performed only cover `status`, `gas_used`, `write_set` hash (`state_change_hash`) and `event_root_hash`: [3](#0-2) 

`TransactionInfoV1` (and the position-state variant) additionally carry `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, which are the authenticated commitments to the full Jellyfish Merkle state root, the hot-state root, and (per `do_state_checkpoint.rs`) the "position" state tree root used for the trading-native feature. [4](#0-3) 
These roots are exactly the values covered by the accumulator/ledger-info signature chain, i.e., they are the proof-bearing, authenticated state commitments for the ledger. `replay_on_archive.rs` calls `ensure_match_transaction_info` as its correctness check when replaying historical transactions against archive data, so any place where the write set or the derived state root diverges from consensus-committed values but the write-set bytes still happen to hash the same (or the divergence is confined to a resource that only affects the JMT root, not this transaction's own write set, e.g. sharded/positional state features), would go undetected.

The `position_state_checkpoint_hash` field is produced by `do_state_checkpoint.rs`'s `get_position_checkpoint_hashes`, a Merkle root over positional writes (native margin/positions state used by "trading-native"/`COMPUTE_TRADING_NATIVE_STATE_ROOTS`). [5](#0-4) 
Since `ensure_match_transaction_info` never compares this hash (nor `state_checkpoint_hash`/`hot_state_checkpoint_hash`) against the recomputed value, a bug in computing this positional/state-checkpoint root during re-execution (e.g. from a code change, upgrade, or hardware/software fault) will not be caught by replay verification, silently masking hard-fork-class divergence between different nodes' internally derived state roots even though `write_set_hash`/`event_root_hash` match.

### Impact Explanation
Replay-verify (`db-tool replay_on_archive`) and any downstream tooling relying on `ensure_match_transaction_info` is the safety net used to detect a mismatch between locally re-executed VM output and the already-committed, authenticated `TransactionInfo`. Because the state-checkpoint/hot-state/position-checkpoint hashes are excluded from the comparison, a real divergence in the derived state root (the value that is ultimately signed over by validators via the transaction accumulator and ledger info) can pass replay verification undetected. This is a proof-integrity gap: an authenticated commitment (the state root embedded in `TransactionInfo`) is not actually checked against the locally computed value, defeating the primary purpose of this verification path and potentially masking a hard-fork-causing divergence in state computation before it's caught.

### Likelihood Explanation
This requires (a) a bug elsewhere in state-checkpoint/hot-state/positional-root computation to actually exist, and (b) reliance on this comparator to catch it. The gate itself is unconditionally present in the code path regardless of feature flags today, and the comment states the intent to fix it "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`", implying the trading-native feature is not fully gated on this validation yet. Likelihood of the underlying computation bug being present/triggered is uncertain; the confirmed defect is the missing self-check itself, which is code-verifiable as written today.

### Recommendation
In `ensure_match_transaction_info`, compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on `txn_info`) against locally recomputed roots before returning `Ok(())`, matching the existing pattern used for `write_set_hash` and `event_root_hash`. Ensure `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is not enabled until this validation is in place.

### Proof of Concept
Not independently exploitable as a single-shot PoC without an accompanying root-computation bug; the finding is the absence of an integrity check itself, directly visible in the function body: `ensure_match_transaction_info` returns `Ok(())` after only checking `status`, `gas_used`, `write_set` hash, and `event_root_hash`, per the code and comment cited above. [1](#0-0) 

**Uncertainty note:** I was unable to fully trace whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is currently reachable/enabled on mainnet, or whether other call sites independently re-validate `state_checkpoint_hash` outside of `ensure_match_transaction_info` (e.g., during normal consensus-path commit, as opposed to replay-verify tooling), due to iteration limits. This distinction affects whether the impact is confined to offline replay-verify tooling (lower severity) versus a live consensus/commit-path gap (higher severity) — I could not confirm which within the available search budget.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L98-190)
```rust
    )> {
        let _timer = OTHER_TIMERS.timer_with(&["get_position_checkpoint_hashes"]);

        let num_txns = execution_output.to_commit.len();
        let first_version = execution_output.first_version;
        let last_checkpoint_index = execution_output
            .to_commit
            .state_update_refs()
            .last_inner_checkpoint_index();
        let base_summary = persisted.summary();
        // No in-memory parent at genesis / first block after enabling: seed
        // from the pre-committed position tip (covers committed writes the
        // merklized snapshot may lag).
        let parent_latest =
            parent.map_or_else(|| persisted.base().latest().clone(), |p| p.latest().clone());
        let parent_last_checkpoint = parent.map_or_else(
            || persisted.base().last_checkpoint().clone(),
            |p| p.last_checkpoint().clone(),
        );

        // Empty chunk: nothing to extend (avoids the `num_txns - 1` underflow).
        if num_txns == 0 {
            let summary = LedgerWithSummary::from_latest_and_last_checkpoint(
                parent_latest,
                parent_last_checkpoint,
            );
            return Ok((summary, vec![]));
        }

        // Collapse position writes (latest-per-key) over a version range into
        // SMT leaf updates.
        let collect = |range: std::ops::Range<usize>| -> Vec<(HashValue, PositionSlot)> {
            let mut latest: HashMap<HashValue, PositionSlot> = HashMap::new();
            for i in range {
                for (key, op) in execution_output.to_commit.transaction_outputs[i]
                    .write_set()
                    .native_position_iter()
                {
                    let value_hash = op.as_write_op().as_state_value_opt().map(StateValue::hash);
                    latest.insert(key.hash(), PositionSlot {
                        state_key: key.clone(),
                        value_hash,
                        value: None,
                    });
                }
            }
            latest.into_iter().collect()
        };

        let (new_latest, new_last_checkpoint) = if let Some(ci) = last_checkpoint_index {
            let checkpoint_version = first_version + ci as u64;
            let new_ckpt = parent_latest.extend(
                checkpoint_version,
                collect(0..ci + 1),
                base_summary,
                persisted,
            )?;
            if ci + 1 == num_txns {
                (new_ckpt.clone(), new_ckpt)
            } else {
                let last_version = first_version + num_txns as u64 - 1;
                let new_latest = new_ckpt.extend(
                    last_version,
                    collect(ci + 1..num_txns),
                    base_summary,
                    persisted,
                )?;
                (new_latest, new_ckpt)
            }
        } else {
            // No checkpoint in this chunk: only the latest advances.
            let last_version = first_version + num_txns as u64 - 1;
            let new_latest = parent_latest.extend(
                last_version,
                collect(0..num_txns),
                base_summary,
                persisted,
            )?;
            (new_latest, parent_last_checkpoint)
        };

        // Per-tx hash vector + known-hash validation (shared with main/hot state).
        let hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_position_state_checkpoints,
            new_last_checkpoint.root_hash(),
            "position_state",
        )?;

        let summary =
            LedgerWithSummary::from_latest_and_last_checkpoint(new_latest, new_last_checkpoint);
        Ok((summary, hashes))
    }
```
