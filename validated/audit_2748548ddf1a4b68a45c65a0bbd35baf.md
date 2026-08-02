### Title
`ensure_match_transaction_info` skips checkpoint/state-root fields, letting replay verification accept a computed state root that diverges from the authenticated one - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the invariant check used by replay/verification tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-debugger`, `cli/src/commands.rs`, `chunk_executor`) to confirm that a freshly re-executed `TransactionOutput` matches the authenticated `TransactionInfo` stored on-chain/in the accumulator. It only compares status, gas used, write-set hash (`state_change_hash`) and event root hash. It never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that actually commit to the resulting global state root (JMT root / hot-state root / native-position root). This mirrors the reported bug pattern: a validation routine that is supposed to enforce a cumulative/complete invariant (here, "the recomputed ledger state matches the authenticated one") but only checks a subset of the relevant quantity, allowing a real divergence to slip through undetected.

### Finding Description
`ensure_match_transaction_info` (types/src/transaction/mod.rs, around line 2139) is documented by the code authors themselves as incomplete:

```rust
pub fn ensure_match_transaction_info(
    &self,
    version: Version,
    txn_info: &TransactionInfo,
    expected_write_set: Option<&WriteSet>,
    expected_events: Option<&[ContractEvent]>,
) -> Result<()> {
    ...
    let write_set_hash = CryptoHash::hash(self.write_set());
    ensure!(write_set_hash == txn_info.state_change_hash(), ...);
    ...
    let event_root_hash = InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash;
    ensure!(event_root_hash == txn_info.event_root_hash(), ...);

    // TODO(trading-native): this comparator ignores the checkpoint hashes
    // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
    // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
    // replay even when the authenticated position state root diverges from
    // local execution. Validate the checkpoint hashes here before enabling
    // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
    Ok(())
}
``` [1](#0-0) 

The function checks the write-set hash (`state_change_hash`) and event root, but it never checks `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` against anything recomputed locally — because `TransactionOutput` (the VM output struct) does not even carry a recomputed checkpoint hash to compare against; those roots are computed later by `DoStateCheckpoint`/accumulator logic, not by this comparator. The comment explicitly states the impact: "replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution."

`storage/db-tool/src/replay_on_archive.rs::execute_and_verify` uses exactly this function as the sole per-transaction correctness gate during replay against archived history:
```rust
if let Err(err) = executed_outputs[idx].ensure_match_transaction_info(
    version,
    &expected_txn_infos[idx],
    Some(&expected_writesets[idx]),
    Some(&expected_events[idx]),
) { ... }
``` [2](#0-1) 

The same function is used by `aptos-debugger` and `cli/src/commands.rs` and `execution/executor/src/chunk_executor/mod.rs` for similar authoritative-vs-recomputed checks (grep hits recorded, not independently re-verified line-by-line here due to iteration limits).

### Impact Explanation
The checkpoint hashes (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) are the fields in `TransactionInfoV0`/`TransactionInfoV1` that commit to the world-state Sparse-Merkle/Jellyfish root, the hot-state root, and (once the trading-native/`compute_trading_native_state_roots` feature is enabled) the native-position state root — these are exactly the "committed state" and "proof root" values the State-Integrity Gate calls out. Because `ensure_match_transaction_info` silently skips them, any tool or path relying on this function as its correctness oracle (most notably `replay_on_archive`, which is the tool operators use to validate that historical/archived ledger data actually corresponds to correct VM execution) can report "PASS" even when the locally recomputed state root for a transaction differs from the authenticated on-chain root. This is precisely the "wrong accumulator root / proof accepted as valid" and "authenticated API/state output bound to the wrong root" class of bug the gate targets — a divergence in committed ledger state that the verification tooling is blind to.

The severity is elevated by the code comment itself flagging that this gap must be closed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" — i.e. the authors are aware this is currently an open, unpatched integrity hole gating a feature rollout (native-position/trading state roots) tied to consensus-critical state commitment. Until fixed, replay-verify integrity guarantees for state/hot-state/position roots do not hold.

### Likelihood Explanation
This is not a hypothetical: the gap is present in shipped code and reachable today by anyone running `replay_on_archive`/`aptos-debugger`/`cli` replay-verification against any historical segment where a bug (VM nondeterminism, storage bug, bad backup/restore, or malicious archive tampering with only non-committed fields left intentionally consistent) causes the state root to diverge while write-set hash and event root still match by coincidence or because the divergence originates downstream of write-set application (e.g., in `DoStateCheckpoint`/proof construction rather than in the write set itself). Because state-checkpoint hashes are computed independently of the write-set hash (via SMT computation over applied writes plus prior state), a bug in state application, hot-state merge, or position-state merge logic would not be caught by this comparator at all — it requires no adversarial trigger, only an execution/storage divergence bug elsewhere, which the comparator is specifically meant to catch and currently cannot.

### Recommendation
Extend `ensure_match_transaction_info` (and its `TransactionOutput`/`TransactionInfo` inputs) to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` whenever those fields are populated by `txn_info`, analogous to how `state_change_hash` and `event_root_hash` are already checked. This requires plumbing the recomputed checkpoint hash(es) (produced by `DoStateCheckpoint`) into the comparator call sites (`replay_on_archive.rs`, `chunk_executor/mod.rs`, `aptos-debugger`, `cli/src/commands.rs`) so the check is complete before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any reliance on this function as a correctness gate) is enabled.

### Proof of Concept
Not independently reproduced with a live divergence (would require constructing a state-application bug or corrupted archive where the write-set/event hashes match but the resulting SMT/hot-state/position root differs, then running `replay_on_archive` to confirm a false "verified" result). The root cause and exact skipped invariant are demonstrated directly by the function body and its own TODO comment cited above; further empirical reproduction would need a running Devin session with DB tooling access, which is outside this ask-only investigation's scope.

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
