### Title
`ensure_match_transaction_info` silently skips checkpoint-hash verification, letting replay/db-tool verification pass over a diverged state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verification tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) to check that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` fetched/committed from the ledger. It checks status, gas, write-set hash (state_change_hash), and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that summarize the entire post-transaction (or post-block) state/hot-state/position Merkle roots. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` computes and compares only three integrity values between the locally produced `TransactionOutput` and the trusted `TransactionInfo`: the execution status, gas used, write-set hash (`state_change_hash`), and event root hash. [2](#0-1) 

It never reads or compares `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, even though `TransactionInfoV1` carries all three as first-class, hashed fields committed into the transaction accumulator leaf. [3](#0-2) 

The comment left in the code by the maintainers explicitly acknowledges the gap:
"this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution." [4](#0-3) 

These checkpoint hashes are produced by `DoStateCheckpoint::run`, which independently computes `state_checkpoint_hashes`, and — only when `execution_output.hot_state_root_in_txn_info` is set — `hot_state_checkpoint_hashes`, and — only when `compute_trading_native_state_roots` is set — `position_state_checkpoint_hashes`. [5](#0-4) 

Because `ensure_match_transaction_info` never re-derives or compares these roots against the authenticated `TransactionInfo`, a divergence between the locally computed state/hot-state/position Jellyfish-Merkle root and the on-chain committed root at the same version is not detected by this check. State-checkpoint hashes are exactly the kind of "committed state that differs from the correct VM result" invariant the state-integrity gate calls out, since they are the anchor by which a full state snapshot (and thus every account/resource value) is cryptographically bound to a specific ledger version.

### Impact Explanation
This function is the core assertion used by `replay_on_archive` (db-tool) and the `aptos-debugger`/CLI replay-verify commands, tools whose entire purpose is to detect state divergence between independent re-execution and the authenticated, consensus-committed ledger. Because checkpoint-hash mismatches are silently skipped, a bug that corrupts the state Merkle root (or the newer hot-state / position-state roots) at a checkpoint boundary — while leaving individual write-set hashes, gas, and events unaffected for the specific transactions inspected — would not be caught by replay verification. This directly matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" impact category: a genuine state-commitment bug (e.g., in the sharded state-merkle commit path, hot-state pruning, or the newer position-state tree) could go undetected by the very tooling meant to catch it before or after a hard fork, delaying detection of a chain split or of corrupted archive data used for bootstrapping other nodes.

### Likelihood Explanation
The bug is a straightforward logic omission (missing invariant check) rather than a computation error, so it is deterministic and always present, but it is a second-order/"defense-in-depth" tool rather than a consensus-critical path: it does not by itself corrupt state, it only fails to detect state corruption when it occurs elsewhere. Note that `hot_state_checkpoint_hash` and `position_state_checkpoint_hash` require newer feature flags (`TRANSACTION_INFO_V1`, `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) to be populated; `state_checkpoint_hash` (the classic per-checkpoint state Merkle root, `TransactionInfoV0`/`V1`) is always populated on mainnet today, and that field is also silently skipped, so the gap affects every replay-verify invocation, not only the newer trading-native feature.

### Recommendation
Extend `ensure_match_transaction_info` to independently compute or receive the expected state/hot-state/position checkpoint roots for checkpoint transactions and assert equality with `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()` whenever the corresponding value is `Some`, mirroring the existing `write_set_hash` / `event_root_hash` checks. At minimum, gate the newer trading-native root before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, as the existing TODO already recommends, and also close the pre-existing gap for `state_checkpoint_hash`.

### Proof of Concept
Not independently exploitable as a state-corruption primitive by itself — it is a missing-check bug. Demonstration path:
1. Run `replay_on_archive` (or the aptos-debugger replay-verify path) over a version range that includes a checkpoint transaction.
2. Construct/obtain a `TransactionOutput`/state whose write-set hash, gas, status, and events match the trusted `TransactionInfo`, but whose resulting state Merkle root (or hot-state/position root) differs from `txn_info.state_checkpoint_hash()` (e.g., due to a separate, independent state-commit bug corrupting only a checkpoint-boundary root without touching this transaction's own write set).
3. Observe that `ensure_match_transaction_info` returns `Ok(())` since it only compares status/gas/write_set_hash/event_root_hash and never reads `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, confirming the tool reports a false "successful replay" despite the divergent checkpoint root.

Note: I was unable to fully trace whether any other caller in the commit/consensus path (outside `aptos-debugger`, CLI, and `replay_on_archive`) performs an independent checkpoint-root check that would compensate for this gap; my search only found these three callers of `ensure_match_transaction_info`.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L42-83)
```rust
        let last_checkpoint = state_summary.last_checkpoint();

        let state_checkpoint_hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_state_checkpoints,
            last_checkpoint.root_hash(),
            "state",
        )?;
        let hot_state_checkpoint_hashes = execution_output
            .hot_state_root_in_txn_info
            .then(|| {
                Self::get_state_checkpoint_hashes(
                    execution_output,
                    known_hot_state_checkpoints,
                    last_checkpoint.hot_root_hash()?,
                    "hot_state",
                )
            })
            .transpose()?;

        let (position_state_summary, position_state_checkpoint_hashes) =
            if execution_output.compute_trading_native_state_roots {
                let persisted = persisted_position_state_summary
                    .expect("persisted position summary required when feature on");
                let (summary, hashes) = Self::compute_position_checkpoint(
                    execution_output,
                    parent_position_state_summary,
                    persisted,
                    known_position_state_checkpoints,
                )?;
                (Some(summary), Some(hashes))
            } else {
                (None, None)
            };

        Ok(StateCheckpointOutput::builder()
            .state_summary(state_summary)
            .state_checkpoint_hashes(state_checkpoint_hashes)
            .maybe_hot_state_checkpoint_hashes(hot_state_checkpoint_hashes)
            .maybe_position_state_summary(position_state_summary)
            .maybe_position_state_checkpoint_hashes(position_state_checkpoint_hashes)
            .build())
```
