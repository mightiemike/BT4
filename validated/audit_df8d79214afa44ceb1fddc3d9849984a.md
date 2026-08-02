Based on my investigation, I found one candidate worth flagging, though it requires an important caveat: the gap is behind a feature flag not yet enabled, and I could not fully trace whether `ensure_match_transaction_info` is the *only* verification gate for the `ChunkExecutor::apply_transaction_outputs` (state-sync) path, since I ran out of tool calls before reading `chunk_executor/mod.rs` around the actual `ensure_match_transaction_info` call site and the `apply` path in full.

### Title
`ensure_match_transaction_info` skips checkpoint-hash validation, allowing accepted transaction outputs to diverge from authenticated ledger state during output-apply/replay - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` (used by the chunk executor's output-apply path, `aptos-debugger`, `cli`, and `db-tool/replay_on_archive`) validates status, gas, write-set hash, and event-root hash against the authenticated `TransactionInfo`, but explicitly skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — fields that are part of the consensus-signed `TransactionInfo` hash committed into the transaction accumulator.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  checks that the applied `TransactionOutput`'s status, gas used, write-set hash, and event root hash equal the corresponding fields inside the trusted `TransactionInfo` supplied with an accumulator proof. However, the function's own comment admits it never validates the checkpoint hashes: [2](#0-1)  states that "replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is called from `execution/executor/src/chunk_executor/mod.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`, and `storage/db-tool/src/replay_on_archive.rs` [3](#0-2) . Separately, the `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` fields are treated as authenticated, consensus-committed values elsewhere in the codebase — e.g., `DoStateCheckpoint` in the normal chunk-execution ledger-update flow does compare computed checkpoints against `known_state_checkpoints`/`known_hot_state_checkpoints`/`known_position_state_checkpoints` [4](#0-3) , confirming these hashes are meant to be a hard integrity check on the resulting state, not merely informational.

The gap is that `ensure_match_transaction_info` — a separate, narrower verification helper — is documented as not enforcing this invariant, meaning any code path relying solely on it (rather than full `DoStateCheckpoint` re-derivation) would accept a `TransactionOutput` whose actual resulting state (including the native-position Merkle tree, gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) does not match what validators committed.

### Impact Explanation
If this comparator is the verification gate for any output-apply/replay path (e.g., `db-tool`'s `replay_on_archive`, `aptos-debugger`, or a future/expanded use in state sync's "apply transaction outputs" mode), a divergence between locally derived state (state root, hot-state root, or native-position root) and the consensus-committed value would go undetected. That is a direct authenticated-proof/state-commitment integrity gap: silently accepting output that doesn't match the signed ledger commitment.

### Likelihood Explanation
Low-to-Medium today: the affected feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is not yet enabled per the code's feature-flag gating [5](#0-4) , and the comment in the code itself flags this as a known TODO to fix "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" [6](#0-5) . I was not able to confirm, within the remaining budget, whether `ensure_match_transaction_info` is actually invoked on the mainnet-critical state-sync "apply outputs" hot path (versus only debugging/replay tooling), which materially affects whether this reaches the "matters on mainnet" bar required by the gate.

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present) against locally recomputed values before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, and audit all call sites (`chunk_executor`, `aptos-debugger`, `cli`, `db-tool/replay_on_archive`) to confirm none of them rely on this function as their sole state-integrity gate.

### Proof of Concept
Not independently reproducible from static inspection alone — the code path is gated behind the not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` flags, and I could not verify within budget whether any currently-enabled mainnet path relies exclusively on `ensure_match_transaction_info` (rather than the full `DoStateCheckpoint` recomputation) for `state_checkpoint_hash` validation. Given this uncertainty and that the gap is self-documented as a pending TODO rather than a silently-introduced defect, I present this with reduced confidence rather than as a confirmed exploitable vulnerability.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2196)
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

```

**File:** types/src/transaction/mod.rs (L2197-2203)
```rust
        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
```

**File:** execution/executor/src/chunk_executor/mod.rs (L373-413)
```rust
        let txn_infos = chunk_verifier.transaction_infos();
        let known_state_checkpoints = Some(
            txn_infos
                .iter()
                .map(|t| t.state_checkpoint_hash())
                .collect_vec(),
        );
        let known_hot_state_checkpoints =
            output.execution_output.hot_state_root_in_txn_info.then(|| {
                txn_infos
                    .iter()
                    .map(|t| t.hot_state_checkpoint_hash())
                    .collect_vec()
            });
        let compute_trading_native_state_roots =
            output.execution_output.compute_trading_native_state_roots;
        let known_position_state_checkpoints = compute_trading_native_state_roots.then(|| {
            txn_infos
                .iter()
                .map(|t| t.position_state_checkpoint_hash())
                .collect_vec()
        });
        let position_persisted = compute_trading_native_state_roots
            .then(|| ProvablePositionStateSummary::new_persisted(self.db.reader.as_ref()))
            .transpose()?;
        let state_checkpoint_output = DoStateCheckpoint::run()
            .execution_output(&output.execution_output)
            .parent_state_summary(&parent_state_summary)
            .persisted_state_summary(&ProvableStateSummary::new_persisted(
                self.db.reader.as_ref(),
            )?)
            .maybe_known_state_checkpoints(known_state_checkpoints)
            .maybe_known_hot_state_checkpoints(known_hot_state_checkpoints)
            // Parent position summary is chained across chunks by the commit
            // queue (seeded from the pre-committed position tip); the persisted
            // base supplies cold-key proofs. The known-hash check validates the
            // computed root against the committed TransactionInfos.
            .maybe_parent_position_state_summary(parent_position_state_summary.as_ref())
            .maybe_persisted_position_state_summary(position_persisted.as_ref())
            .maybe_known_position_state_checkpoints(known_position_state_checkpoints)
            .build()?;
```

**File:** aptos-move/framework/move-stdlib/sources/configs/features.move (L949-955)
```text
    /// When enabled, execution computes the trading-native state roots and commits them to
    /// `TransactionInfoV1`, so they are consensus-verified. Requires `TRANSACTION_INFO_V1`.
    /// Covers the native-position tree today and is intended to cover the other trading-native
    /// trees as they are added. Enabling it first commits the (empty-tree) roots to transaction
    /// info; the actual Move-side writes to those trees are gated by separate flags.
    /// Lifetime: permanent
    const COMPUTE_TRADING_NATIVE_STATE_ROOTS: u64 = 122;
```
