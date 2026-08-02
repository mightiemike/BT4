### Title
Replay-verify comparator ignores checkpoint hashes, letting corrupted state/hot-state/position roots pass verification - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` — the function used by chunk executor and replay/debug tooling to confirm a locally re-executed `TransactionOutput` matches an already-committed, accumulator-authenticated `TransactionInfo` — checks status, gas, write-set hash, and event-root hash, but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. These three hashes are exactly the fields that feed the ledger's `TransactionInfo` hash, which is what gets appended to the transaction accumulator, so a state-summary/position-tree divergence that changes these fields is silently accepted as "verified".

### Finding Description
`ensure_match_transaction_info` is Aptos's local analog of "trusting the wrong on-chain object" — it authenticates a `TransactionOutput` against a `TransactionInfo`, similar in spirit to how the external report's `getLiquidityAmounts()` trusted the wrong pool address instead of the one actually tied to `tokenId`. Here, the comparator is supposed to prove the locally computed output is consistent with the previously accumulator-committed `TransactionInfo`, but it only compares a subset of the fields that are hashed into that `TransactionInfo`: [1](#0-0) 

Specifically it verifies `status`, `gas_used`, `write_set` hash (`state_change_hash`), and `event_root_hash`, but the code contains its own acknowledgment of the gap:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution.
``` [2](#0-1) 

These checkpoint hashes are not decorative — `TransactionInfoV1` carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as first-class fields that participate in the `TransactionInfo`'s `CryptoHash`, and that hash is what `DoLedgerUpdate::run` appends into the transaction accumulator: [3](#0-2) [4](#0-3) 

The checkpoint hashes themselves are computed from separate integrity-sensitive structures — `LedgerStateSummary` (state/hot-state Merkle roots) and the native-position `LedgerWithSummary<PositionStateWithSummary>` tree — assembled in `DoStateCheckpoint::run`: [5](#0-4) 

`ensure_match_transaction_info` is invoked from the chunk executor and CLI/debugger replay-verify paths (`execution/executor/src/chunk_executor/mod.rs`, `aptos-move/cli/src/commands.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`) as the integrity gate that decides whether locally re-executed output "matches" what is already committed/accumulator-authenticated. Because it skips the checkpoint-hash fields, any code path that produces a diverging `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — for example a bug in `LedgerStateSummary::update`, the hot-state root computation, or the new native-position tree (`ProvablePositionStateSummary`/`compute_position_checkpoint`) — will not be caught by this check, even though the resulting `TransactionInfo` hash (and therefore the accumulator root) is objectively wrong relative to the authenticated ledger.

### Impact Explanation
This breaks the "authenticated proof stays bound to the right root" invariant: replay/verification tooling that relies on `ensure_match_transaction_info` (chunk executor commit-time validation and CLI/debugger replay-verify against archived data) can report a transaction as correctly verified while the transaction's checkpoint-hash fields — and hence its contribution to the transaction accumulator root — silently diverge from the authenticated chain state. Since `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and `HOT_STATE_ROOT_IN_TXN_INFO` are both documented as intended to be "consensus-verified" (per the feature-flag doc comments in `types/src/on_chain_config/aptos_features.rs`), a bug in either subsystem would go undetected by exactly the mechanism meant to catch it, undermining confidence that replayed/restored ledger state is provably correct. This is a proof-integrity gap in the verification of committed state, not an out-of-scope oracle or DoS issue.

### Likelihood Explanation
Likelihood is tied to whether/when the state-checkpoint or new position-tree computations diverge from consensus-committed values (e.g., due to a bug elsewhere in `DoStateCheckpoint`/`ProvablePositionStateSummary`, or a hard-fork/version-skew scenario). The gap itself is unconditional and already flagged in-repo as a known TODO, but currently `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is a newer, feature-gated code path, so real-world triggering likelihood depends on that feature's rollout status and correctness of the position-tree logic — both areas not fully auditable from the available snippets.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash` (when `HOT_STATE_ROOT_IN_TXN_INFO` applies), and `position_state_checkpoint_hash` (when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` applies) against the locally recomputed values before treating replay as verified, closing the gap noted in the existing TODO comment.

### Proof of Concept
Not independently reproducible from static analysis alone — the finding is the verification-function's own documented omission (`types/src/transaction/mod.rs:2197-2204`) combined with confirming that the skipped fields are (a) part of `TransactionInfo`'s hash that is committed to the accumulator (`do_ledger_update.rs:35-48`) and (b) computed by separate, independently-fallible logic (`do_state_checkpoint.rs:36-75`). I could not fully trace a concrete bug in `DoStateCheckpoint`/`ProvablePositionStateSummary` that would actually produce a wrong checkpoint hash in practice within the available tool budget, so triggering conditions for real divergence remain unverified.

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L35-48)
```rust
        let (transaction_infos, transaction_info_hashes) = Self::assemble_transaction_infos(
            &execution_output.to_commit,
            execution_output.transaction_info_v1,
            &state_checkpoint_output.state_checkpoint_hashes,
            state_checkpoint_output
                .hot_state_checkpoint_hashes
                .as_deref(),
            state_checkpoint_output
                .position_state_checkpoint_hashes
                .as_deref(),
        );

        // Calculate root hash
        let transaction_accumulator = Arc::new(parent_accumulator.append(&transaction_info_hashes));
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L36-75)
```rust
        let state_summary = parent_state_summary.update(
            persisted_state_summary,
            &execution_output.hot_state_updates,
            execution_output.to_commit.state_update_refs(),
        )?;

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
```
