### Title
`ensure_match_transaction_info` skips checkpoint-hash validation, letting replay-verify accept a divergent state/hot-state/position root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity gate used by chunk-executor replay verification (`verify_execution` in `execution/executor/src/chunk_executor/mod.rs`) and by the `db-tool replay-on-archive` / `aptos-debugger` tooling to confirm that a locally re-executed transaction produced the same result as what is recorded (and accumulator-committed) in the authoritative `TransactionInfo`. The function checks `status`, `gas_used`, `state_change_hash` (write-set hash) and `event_root_hash`, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that carry the Sparse-Merkle state root, hot-state root, and native-position state root that are hashed into `TransactionInfo` and ultimately into the transaction accumulator that ledger-info signatures attest to.

### Finding Description
`ensure_match_transaction_info` is meant to be the authoritative check that a re-executed/replayed transaction matches the committed `TransactionInfo` before that data is treated as trusted: [1](#0-0) 

It validates 4 of the ~7 committed fields (status, gas, write-set hash, event root), but the developers' own comment documents that the checkpoint hashes are intentionally skipped: [2](#0-1) 

These skipped fields are not cosmetic — they are consensus-committed state roots. `TransactionInfoV1` carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as first-class fields that are hashed into the `TransactionInfo` (via `BCSCryptoHash`) and therefore into the transaction accumulator root that ledger infos sign: [3](#0-2) 

The only place these hashes are produced during real execution is `DoLedgerUpdate::assemble_transaction_infos`, which pulls `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` from `StateCheckpointOutput` and bakes them into the persisted `TransactionInfo`: [4](#0-3) 

`ensure_match_transaction_info` is invoked from the chunk-executor's `verify_execution`, which is the code path responsible for confirming that replayed/re-executed output matches what's on record before it is treated as verified: [5](#0-4) 

and from `storage/db-tool/src/replay_on_archive.rs` and `aptos-move/aptos-debugger/src/aptos_debugger.rs` (both of which call `ensure_match_transaction_info` as their pass/fail criterion for replay).

Because the comparator silently ignores `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, any divergence between the state root a validator locally computes at replay time and the one already committed/signed in the ledger will not be flagged. This is structurally the same integrity failure pattern as the external report: a security check (here, "the replayed output must match the committed record") is nominally enforced, but a side channel (the checkpoint/state-root fields) bypasses the restriction, exactly as delegation bypassed the voting restriction in the DeXe report.

### Impact Explanation
This breaks the state-commitment/proof-integrity invariant that "committed state that differs from the correct VM result... must be detectable at replay/restore," listed explicitly as an in-scope impact. A bug in state-checkpoint-root computation (Sparse Merkle Tree root), hot-state root, or the newer native-position state root (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) would not be caught by `verify_execution`/`replay_on_archive`, meaning:
- A consensus-vs-replay divergence in these roots (e.g., caused by a bug elsewhere in `DoStateCheckpoint` or `update_hot_state_summary`) would pass replay-verification tooling as "successful", even though the locally recomputed ledger state differs from the authenticated one.
- This is a hard-fork-class detection gap: replay/verify is one of the last lines of defense for catching state divergence bugs before/after a hard fork, and it is blind to exactly the fields (state/hot-state/position roots) most likely to diverge due to a subtle executor bug.

This is a real, currently-shipped gap (not hypothetical) as evidenced by the developers' own TODO acknowledging the risk with `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

### Likelihood Explanation
The gap is triggered any time `verify_execution`, `replay_on_archive`, or the debugger's transaction-info check is used to validate a chunk containing `state_checkpoint_hash` (always present when checkpoints occur), or when `HOT_STATE_ROOT_IN_TXN_INFO` / `COMPUTE_TRADING_NATIVE_STATE_ROOTS` features are enabled. No attacker action is needed to trigger the missing check — it is unconditionally skipped for every call; it only becomes an actual security failure once any state-root computation bug exists elsewhere, at which point this comparator's silence prevents detection.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally computed equivalents (accepting `None` only when the on-chain feature that populates each field is disabled), as the existing TODO already recommends, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in production.

### Proof of Concept
No exploit is required to demonstrate the gap — it is a code-level omission visible via inspection:
1. `execution/executor/src/chunk_executor/mod.rs::verify_execution` calls `txn_out.ensure_match_transaction_info(version, txn_info, Some(write_set), Some(events))` as the sole correctness gate for a replayed chunk.
2. `ensure_match_transaction_info` (types/src/transaction/mod.rs:2139-2204) never reads `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`.
3. Construct (in a test) a `TransactionOutput`/`TransactionInfo` pair where the write-set hash, event root, gas, and status all match, but where the state-checkpoint hash that would be computed by `DoStateCheckpoint` for the given write set differs from `txn_info.state_checkpoint_hash()` (e.g., by mutating the persisted `TransactionInfo`'s checkpoint hash while keeping everything else identical). `ensure_match_transaction_info` returns `Ok(())` despite the state root mismatch, confirming replay-verification would falsely report success.

I was not able to execute this test in the sandbox (no build/test tooling available in this session); the finding is based on direct code inspection of the comparator, its call sites, and the fields it omits, plus the developers' own TODO confirming the gap's relevance to `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L685-706)
```rust
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
