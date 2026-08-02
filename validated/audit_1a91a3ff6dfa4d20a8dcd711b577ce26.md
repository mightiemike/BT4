## Finding

### Title
Replay-verification (`ensure_match_transaction_info`) silently accepts a wrong state root, masking state-commitment divergence - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by Aptos's replay/verification tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) to confirm that re-executing an archived transaction produces the same result as the authenticated `TransactionInfo` pulled from backup/archive storage (itself bound to a signed `LedgerInfo` via an accumulator proof). The function checks status, gas used, write-set hash, and event root hash, but it never checks `state_checkpoint_hash` (nor `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`). This mirrors the external report's root cause pattern — a proof/commitment binding is silently dropped or mismatched — except here the missing check is in Aptos's own replay-verify commitment binding rather than an oracle's decimal/token binding.

### Finding Description
`ensure_match_transaction_info` explicitly compares only a subset of the fields a `TransactionInfo` commits to: [1](#0-0) 

It validates `status`, `gas_used`, `write_set` (`state_change_hash`), and `events` (`event_root_hash`) against the trusted `txn_info`, but the function body's own comment states it "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`)", and no code path in the function reads or compares `txn_info.state_checkpoint_hash()` against a locally recomputed state root.

`TransactionInfo` itself commits to `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as first-class fields that are part of what gets hashed into the transaction accumulator and ultimately the signed `LedgerInfo`: [2](#0-1) 

The consuming tool, `replay_on_archive.rs`, uses `ensure_match_transaction_info` as the sole correctness gate after re-executing a chunk of archived transactions with `AptosVMBlockExecutor`: [3](#0-2) 

Because the checkpoint-hash fields are skipped, a bug that corrupts the Jellyfish Merkle Tree / state-summary computation (in `execution/executor/src/workflow/do_state_checkpoint.rs`, `storage/storage-interface/src/state_store/state_summary.rs`, or the hot-state/position-state summary logic) — while still producing byte-identical write sets, gas, status, and events — would go completely undetected by replay-verify. The tool would report a "successful" replay even though the locally reconstructed state root diverges from the one committed and signed in the archived `LedgerInfo`.

### Impact Explanation
Replay-verify (`replay_on_archive`) and the CLI/debugger equivalents are the primary tooling used to detect state-commitment divergence between a node's local execution and the authenticated, validator-signed history (backups verified against `LedgerInfoWithSignatures`). By omitting `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` comparisons, this safety net cannot catch a class of bugs where write sets/events are correct but the resulting Merkle state root is wrong — exactly the kind of state-integrity break this scan targets (wrong accumulator/state root accepted as consistent). This weakens detection of hard-fork-causing state divergence and corruption of durable ledger data, since the tool that operators and auditors rely on to certify "my execution matches mainnet history" gives a false pass.

### Likelihood Explanation
This is not a hypothetical gap — the code's own TODO comment acknowledges the checkpoint hashes are unchecked, and the omission is triggered on every call to `ensure_match_transaction_info` from `replay_on_archive.rs`, `aptos_debugger.rs`, and `cli/src/commands.rs`, i.e., every normal replay-verify run. No malicious input or privileged access is required; any state-root-level regression (e.g., in hot-state or position-state summary logic, which are newer/actively-changing subsystems per the surrounding code) would be masked whenever write sets/events happen to remain correct.

### Recommendation
Extend `ensure_match_transaction_info` to recompute the local state checkpoint hash(es) (main state, hot state, and, when enabled, position state) after applying the transaction, and compare them against `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`, failing verification on any mismatch — consistent with how `write_set_hash` and `event_root_hash` are already validated. At minimum, this should be enforced before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, and the existing TODO should be resolved rather than left as a known gap in a security-critical verification path.

### Proof of Concept
Not applicable as a standalone exploit — the defect is a missing verification step. It can be demonstrated by: (1) locally introducing (for testing) an off-by-one or ordering bug in `do_state_checkpoint.rs`'s SMT update logic that changes `last_checkpoint.root_hash()` while leaving write sets/events/gas/status identical, then (2) running `replay_on_archive` over a range including that transaction and observing it reports success because `ensure_match_transaction_info` never inspects `state_checkpoint_hash`. [4](#0-3)

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
