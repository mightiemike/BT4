### Title
`ensure_match_transaction_info` does not check state/hot-state/position checkpoint hashes, letting replay-verify accept divergent state roots as valid - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verification tooling to assert that a freshly re-executed `TransactionOutput` matches an already-committed/backed-up `TransactionInfo`. It only compares `status`, `gas_used`, the write-set hash (`state_change_hash`), and the event root hash. It deliberately skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the very fields that authenticate the Sparse-Merkle/hot-state/position roots produced at state-checkpoint boundaries.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates re-executed output against the persisted `TransactionInfo` for status, gas, write-set hash and event root hash, but its own comment states the gap explicitly: [2](#0-1) 

This comparator is reused as the sole state-integrity gate in the archive replay-verification tool, `storage/db-tool/src/replay_on_archive.rs`, where each re-executed transaction output is checked against the expected `TransactionInfo` loaded from the archive: [3](#0-2) 

`TransactionInfo` carries multiple checkpoint-root fields beyond `state_change_hash` — `state_checkpoint_hash` (Sparse Merkle Tree world-state root at checkpoint boundaries) plus, in the V1 variant, `hot_state_checkpoint_hash` and the repurposed `position_state_checkpoint_hash`: [4](#0-3) 

These roots are produced independently from the write-set hash by `DoStateCheckpoint`/`DoLedgerUpdate` during normal commit (`execution/executor/src/workflow/do_ledger_update.rs`, `do_state_checkpoint.rs`), and are threaded into `TransactionInfo` construction as a distinct, feature-gated ("trading-native"/hot-state) axis. Because `ensure_match_transaction_info` never compares these hashes, a local execution that reproduces the correct write set and events but computes a *different* state-checkpoint/hot-state/position root (due to a bug in checkpoint-hash derivation, a feature-flag mismatch, or non-determinism introduced by hot-state/position-state logic) will still pass this check with `Ok(())`. The archive/backup data being validated is only checked for write-set and event-root correctness; the checkpoint root binding to the ledger's actual committed state is left unverified.

### Impact Explanation
This breaks the "hard-fork-only divergence during commit, replay, restore" state-commitment integrity guarantee: replay-verify tooling (`replay_on_archive`, and per its call sites in `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs`) is the primary safety net used to detect state divergence between an execution engine and the historically committed/backed-up chain state. Since the checkpoint-hash fields (which authenticate the Sparse Merkle Tree root, hot-state root, and position-state root) are excluded from comparison, a silent divergence in state-checkpoint computation can pass replay-verification undetected. This directly matches the required-impact category "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Committed state that differs from the correct VM result... corrupts durable ledger data" being masked rather than caught, undermining the very tool meant to catch such corruption before it reaches consensus-critical code paths.

### Likelihood Explanation
The gap is not theoretical: the code's own TODO comment acknowledges it ("this comparator ignores the checkpoint hashes ... so replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution"), and it is unconditionally reachable any time `ensure_match_transaction_info` is invoked without checkpoint-hash validation — which is on every call in `replay_on_archive.rs`, `aptos_debugger.rs`, and `commands.rs`. No attacker interaction is required beyond a latent bug or upgrade-induced divergence in checkpoint/hot-state/position-state hash derivation (e.g. under the gated `COMPUTE_TRADING_NATIVE_STATE_ROOTS`-style feature paths), which would otherwise be exactly the kind of subtle non-determinism replay-verify exists to catch.

### Recommendation
Extend `ensure_match_transaction_info` to also assert equality of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when both sides have them present, respecting the version's `TransactionInfo` variant/feature gating), returning an error on mismatch just as it does today for `state_change_hash` and `event_root_hash`, before enabling/relying on trading-native or hot-state root computation in production replay-verify flows.

### Proof of Concept
Conceptual trace (no external network/state needed to see the gap, only code reading):
1. `replay_on_archive.rs::execute_and_verify` re-executes a chunk of transactions and calls `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_infos[idx], Some(write_set), Some(events))` — [5](#0-4) .
2. Inside `ensure_match_transaction_info`, only `status`, `gas_used`, write-set hash, and event root hash are compared against `txn_info` — [6](#0-5) .
3. If the locally computed `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` (produced elsewhere via `DoStateCheckpoint`, not part of `TransactionOutput` at all) diverges from the value baked into the archived `TransactionInfo`, this function returns `Ok(())` regardless, per the explicit TODO at lines 2197-2202.
4. Therefore replay-verify reports success even though the checkpoint root — the authenticated proof-bearing summary of world state — differs from what was actually committed, satisfying the "authenticated ... proof context bound to the wrong version/root" analog from the state-integrity gate.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L392-405)
```rust
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
```
