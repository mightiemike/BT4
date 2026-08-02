## Finding

### Title
Replay-verification comparator silently ignores state/hot-state/position checkpoint root hashes, allowing a diverged state commitment to pass as "verified" - (`types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the sole comparator that `storage/db-tool/src/replay_on_archive.rs` (the tool backing the `replay-verify` pipeline used to re-execute and validate mainnet/testnet transaction history) uses to decide whether a locally re-executed transaction matches the authoritative, archived `TransactionInfo`. The function checks status, gas used, write-set hash, and event root hash, but explicitly skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the fields that actually commit to the post-execution state (Sparse-Merkle/Jellyfish-Merkle) root. A local execution whose state root diverges from the historically committed one will still be reported as a successful, verified replay.

### Finding Description
`ensure_match_transaction_info` validates transaction status, gas, write-set hash and event root hash against the stored `TransactionInfo`, then returns `Ok(())` without checking the checkpoint hash fields, as its own inline TODO documents: [1](#0-0) 

Specifically the state-change/event checks are present: [2](#0-1) 

but the function returns success immediately afterward, per the author's own comment that the comparator "ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution": [3](#0-2) 

This exact function is invoked as the pass/fail gate inside the `replay_on_archive` verifier's per-transaction check loop, and any mismatch found by it is treated as the sole failure signal (`err`) that halts/reports a chunk as broken: [4](#0-3) 

`TransactionInfo`'s `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` are the fields that actually bind a transaction to the resulting world-state root (Sparse-Merkle-Tree / Jellyfish-Merkle-Tree root, hot-state root, and native-position root respectively): [5](#0-4) 

Because the comparator never checks these three fields, a divergence introduced anywhere in state-checkpoint computation (e.g., a VM/state-checkpoint bug, an execution-path change, a serialization/ordering bug in the SMT/JMT construction, or a bug specific to the newer hot-state/position-state roots) will not be flagged by `replay_on_archive`, even though the write set, events, gas, and status all match. The tool is precisely the safety net intended to catch exactly this class of bug before a release is shipped to validators/full nodes (the comparator is also reused by `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs`, per the earlier grep, extending the same blind spot to other debugging/verification entry points).

### Impact Explanation
`replay_on_archive` / replay-verify is the primary tool used to validate that a candidate binary (post feature-flag change, bug fix, or protocol upgrade) reproduces the exact ledger state of historical mainnet transactions before it's promoted to consensus-critical nodes. Because the checkpoint-hash fields are excluded from the match check, this tool can report "0 failed transactions" (a clean bill of health) even when the state root computed by the candidate binary has silently diverged from the one committed in the authoritative ledger. This is a hard-fork-class integrity gap: an undetected state-root divergence that ships in a release would only manifest later as a consensus/execution disagreement across the validator set, which is exactly the category of impact this gate targets ("Hard-fork-only divergence during commit, replay, restore, or proof verification").

### Likelihood Explanation
This is not a hypothetical: the code comment itself, written by the maintainers, documents the exact scenario ("replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution"), and gates re-enabling stricter behavior behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS`: [6](#0-5) 

The gap is live today (the comparator has already shipped without the checks) and will be triggered any time a bug affects state-checkpoint/hot-state/position-state root computation without also affecting the write set contents, gas, status, or events — a plausible failure mode for bugs specific to state-tree construction, hashing, or the newer hot-state/position-state features rather than plain VM execution.

### Recommendation
Add explicit comparisons of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` between the locally computed checkpoint output and the archived `TransactionInfo` inside `ensure_match_transaction_info` (mirroring the pattern already used for `state_change_hash`/`event_root_hash`), rather than gating this behind an unrelated on-chain feature flag. At minimum, `replay_on_archive`, `aptos-debugger`, and the CLI's replay path should independently verify the recomputed checkpoint root against the value in the archived proof before declaring a chunk/transaction verified.

### Proof of Concept
1. Run `db-tool replay-on-archive` against a historical range where a candidate build has a bug purely in state-checkpoint root computation (e.g., an incorrect key ordering or hashing bug in JMT/hot-state/position-state construction) while leaving write sets, events, gas, and status byte-identical to the authoritative execution.
2. `execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs` calls `ensure_match_transaction_info`, which only compares status/gas/write-set-hash/event-root-hash.
3. Because `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` are never compared, the call returns `Ok(())` and the chunk is marked verified with zero errors, even though the resulting Merkle roots for state, hot-state, or position-state have diverged from the authoritative ledger.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L388-415)
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

        cur_txns.clear();
        cur_persisted_aux_info.clear();
        expected_txn_infos.clear();
        expected_events.clear();
        expected_writesets.clear();

        Ok(None)
    }
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```
