## Title
`TransactionOutput::ensure_match_transaction_info` skips checkpoint-hash verification, allowing chunk/replay verification to accept a divergent state/hot-state/position-state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used to authenticate a locally re-executed `TransactionOutput` against the `TransactionInfo` that was committed to the transaction accumulator (and therefore signed/authenticated by validators). It explicitly checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but its own trailing comment documents that it *does not* check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` [1](#0-0) . This function is invoked directly by the chunk executor's execution-verification path [2](#0-1)  and by the debugger/CLI replay tooling, meaning a divergence in any of the checkpoint state roots between a locally-computed result and the authenticated on-chain `TransactionInfo` will not be flagged as a verification failure through this call path.

### Finding Description
`ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) is the sole comparator used by `verify_execution` in the chunk executor's transaction-output verification loop to confirm that re-executed transactions match previously-committed `TransactionInfo`s pulled from the authenticated ledger [3](#0-2) . It performs four checks — `status`, `gas_used`, `state_change_hash` (write-set hash), and `event_root_hash` — but the function itself carries a `TODO(trading-native)` comment stating it "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution" [4](#0-3) .

`TransactionInfoV1` carries three separate Merkle-root-bearing fields that summarize different halves of ledger state: `state_checkpoint_hash` (main SMT), `hot_state_checkpoint_hash` (hot-state JMT), and `position_state_checkpoint_hash` (the repurposed reserved field for the native-position state root) [5](#0-4) . These are the values authenticated by the ledger accumulator/`LedgerInfo` signatures, and are exactly the kind of proof-bearing fields the state-integrity gate calls out. The comparator that is supposed to bind a locally-recomputed `TransactionOutput` to that authenticated `TransactionInfo` silently skips comparing all three.

### Impact Explanation
If, due to a bug elsewhere in the state-checkpoint computation pipeline (e.g., hot-state JMT commit, position-state SMT extension in `DoStateCheckpoint`, or any future logic touching `compute_trading_native_state_roots`/`hot_state_root_in_txn_info`), a node's locally computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` diverges from the value embedded in the authenticated `TransactionInfo`, this specific verification path (`verify_execution` in the chunk executor, and the CLI/debugger replay tools that call the same function) would not detect the divergence and would report a successful replay/verification. This directly undermines the "authenticated API and proof-bearing responses must stay bound to the right ledger version, root, and object" invariant: a corrupted or incorrect state root can pass as verified through this code path, which is used for execution verification during chunk replay (`verify_execution_mode`) and by `db-tool`'s `replay_on_archive` and the Aptos debugger. This is a proof-integrity gap rather than a rounding bug, but it matches the requested analog class (VM output / transaction info binding surviving executor-to-storage/replay handoff).

### Likelihood Explanation
This is not itself an externally-triggerable exploit by an unprivileged attacker — it is a missing-check defect that widens the blast radius of any *other* bug that could cause a checkpoint-hash mismatch (e.g., a subtle hot-state or position-state computation bug). Its likelihood of causing real harm depends on such an upstream bug existing; absent that, the impact is latent. The code path is real and reachable (not test-only) via `verify_execution`, and the comment in the code confirms the developers are aware this is an outstanding gap that should be closed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" [6](#0-5) , indicating the feature guarding position-state roots is not yet fully protected by this verification.

### Recommendation
Extend `ensure_match_transaction_info` to compare `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` against the corresponding locally-computed roots (threading them in as additional parameters, since `TransactionOutput` alone doesn't carry checkpoint roots — those are computed at the state-checkpoint stage) before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or `hot_state_root_in_txn_info` is enabled in production, and before this comparator is relied upon by any additional replay/verification consumers.

### Proof of Concept
No standalone PoC is provided because triggering the actual divergence requires an independent bug in the state/hot-state/position-state root computation (outside the scope of this function); the defect proven here is the *missing check* itself, directly evidenced by the code and its own TODO comment at [7](#0-6) , and its live call sites at [3](#0-2) .

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

**File:** execution/executor/src/chunk_executor/mod.rs (L684-706)
```rust
        // not `zip_eq`, deliberately
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
