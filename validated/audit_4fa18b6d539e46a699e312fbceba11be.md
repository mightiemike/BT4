## Analysis

The external report's core invariant is: **a proof-bearing/authenticated value must be checked in full before it is trusted to bind to the correct underlying object** (there, the receipt vs. LP token identity; here, the analog is checking that a committed `TransactionOutput` fully matches its `TransactionInfo`).

I traced this to `TransactionOutput::ensure_match_transaction_info` in `types/src/transaction/mod.rs`, which is the authoritative "does this locally-computed output match the authenticated `TransactionInfo` from the accumulator/proof" check used by replay-verification tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`, `execution/executor/src/chunk_executor/mod.rs`).

The function explicitly verifies `status`, `gas_used`, `write_set_hash` (vs `state_change_hash`), and `event_root_hash`, but its own inline `TODO` comment states it **deliberately skips** `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and the newly repurposed `position_state_checkpoint_hash` fields: [1](#0-0) 

### Title
Replay/state-proof verification silently ignores state-checkpoint hash fields in `TransactionOutput::ensure_match_transaction_info` - (File: `types/src/transaction/mod.rs`)

### Summary
`ensure_match_transaction_info` is the function used by db-tool's replay-verification (`replay_on_archive.rs`) and the Aptos debugger/CLI to confirm that a locally re-executed `TransactionOutput` matches the trusted, proof-authenticated `TransactionInfo` fetched from backup/archive. It checks status, gas, write-set hash, and event root hash, but does not check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the corresponding fields of `TransactionInfo`.

### Finding Description
`TransactionInfo` (V0/V1) carries `state_checkpoint_hash` and, in V1, `hot_state_checkpoint_hash` and the repurposed `position_state_checkpoint_hash` field, all of which are part of the accumulator leaf hash committed on-chain: [2](#0-1) 

`ensure_match_transaction_info` is documented as intentionally comparing only status/gas/write-set-hash/event-root-hash, leaving the checkpoint hash fields unchecked: [3](#0-2) 

This function is the sole result-verification primitive used by `replay_on_archive.rs`'s `execute_and_verify`, which re-executes archived transactions and calls it to decide pass/fail of a replay run: [4](#0-3) 

Because the state/hot-state/position checkpoint hashes are excluded from the comparison, a divergence in the *state root produced by execution* (as opposed to the write-set contents) at a checkpoint boundary will not be caught by this tool, even though the transaction's write set and events match exactly. State checkpoint hashes are derived by the executor's own separate merklization step (`do_state_checkpoint.rs`) and are not re-derived from the write set inside `ensure_match_transaction_info`, so this is a genuine gap in that specific proof binding, not a duplicate check.

### Impact Explanation
This is a verification-tooling gap, not a consensus-committed value corruption: block-executor/committed ledger paths still compute and check `TransactionAccumulator` leaf hashes (which include the checkpoint hash fields) via the normal ledger-update path (`do_ledger_update.rs`) and consensus commit-vote comparisons (`buffer_item.rs`). The gap only affects `replay_on_archive` / debugger-style forensic re-verification: it can report "successful replay" even if the locally recomputed state (Merkle/hot-state/native-position) root diverges from the historically committed, ledger-info-signed root, because that divergence is only encoded in the checkpoint-hash fields that this checker skips. This weakens confidence in archive/replay-based fork or corruption detection, but by itself does not let an attacker corrupt consensus-committed state, forge accumulator proofs accepted by validators, or bypass the real accumulator verification used by full nodes/light clients (`AccumulatorProof::verify`, `TransactionInfoWithProof::verify`) which do use the full transaction-info hash.

### Likelihood Explanation
Low likelihood of exploitation as an attack vector (this tool is an internal debugging/replay-verification utility, not part of the consensus or state-sync hot path), but the condition (execution producing a correct write-set/events while diverging in a state-checkpoint root due to a state-computation bug, e.g., hot-state or native-position merklization bug) is realistic given that the codebase explicitly separates state summarization from the write-set/event materialization pipeline. The code's own TODO comment (`"...so replay-verify tooling (e.g. db-tool's replay_on_archive) can report a successful replay even when the authenticated position state root diverges..."`) confirms this is a recognized, currently-unaddressed gap rather than a speculative one.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, and (when available) `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`, against the equivalent locally-computed values before COMPUTE_TRADING_NATIVE_STATE_ROOTS or any feature relying on this check for state-root integrity is enabled, so replay-verification cannot silently pass over a diverged authenticated state root.

### Proof of Concept
Not independently reproducible as a mainnet-exploitable PoC from static analysis: this requires crafting/observing a real execution divergence at a checkpoint boundary (e.g., a hot-state or native-position merklization bug) that leaves write-set/events/gas/status untouched while state-checkpoint hash differs, then running `replay_on_archive` over that version to observe a false "pass." I could not verify with the tools available whether such an underlying state-computation divergence currently exists elsewhere in the codebase — that would require deeper analysis of `do_state_checkpoint.rs` and the native-position merklization path, which was outside the scope I could fully confirm in this session.

### Citations

**File:** types/src/transaction/mod.rs (L2159-2203)
```rust
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
