## Finding: `TransactionOutput::ensure_match_transaction_info` omits validation of state-checkpoint root hashes, letting replay/restore accept an output whose committed state root diverges from the authenticated `TransactionInfo`

### Title
Replay/restore verification (`ensure_match_transaction_info`) does not check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, allowing a divergent state root to pass as a match - (File: `types/src/transaction/mod.rs`)

### Summary
This is a state-commitment analog of the Sherlock report's root cause pattern: an authenticated/committed artifact is validated against only a subset of its constituent fields, so an attacker (or a divergent/malicious data source) can supply mismatched values for the unchecked fields and still have the check pass. Here, `TransactionOutput::ensure_match_transaction_info` — the function used across the replay, restore-verification and debugging tool paths to confirm that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` fetched from an archive/backup — validates status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` checks four fields of the re-executed `TransactionOutput` against the `TransactionInfo` obtained from the trusted backup/ledger proof: status, gas used, the write-set hash against `state_change_hash`, and the event root hash. [2](#0-1) 

It never checks `TransactionInfo::state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that authenticate the Sparse-Merkle-Tree roots of the main state, hot state, and (increasingly relevant) the native-position state used by the trading-native subsystem gated behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS`. This gap is called out explicitly in the code itself: [3](#0-2) 

`TransactionInfoV1` carries `position_state_checkpoint_hash` as a "repurposed reserved field", intended to be consensus-verified once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled. [4](#0-3) [5](#0-4) 

`ensure_match_transaction_info` is the sole per-transaction correctness gate used by `storage/db-tool/src/replay_on_archive.rs` (the archive replay-verify tool run in CI/ops to detect divergence between a node's independent re-execution and the backed-up/authenticated ledger data), as well as by `aptos-move/cli/src/commands.rs` and `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and is also referenced from `execution/executor/src/chunk_executor/mod.rs` (the `TransactionReplayer`/chunk-replay implementation used by backup restore's `replay_transactions` path). [6](#0-5) 

Because this comparator does not check the checkpoint hashes, a `TransactionOutput` whose native-position (or hot-state) Sparse Merkle root diverges from the historically committed/authenticated value in `TransactionInfo` will still be reported as matching — the divergence is silently swallowed instead of surfaced as a hard-fork-class inconsistency.

### Impact Explanation
This breaks the replay/restore proof-integrity invariant required by the Gate: "Hard-fork-only divergence during commit, replay, restore, or proof verification" must be detected, not silently accepted. If a node's local execution of the native-position (or hot-state) tree diverges from the value that was actually committed and authenticated on-chain (e.g., due to a state-computation bug that only manifests for that specific subsystem, or a corrupted/malicious backup archive supplying a wrong `write_set`/write for the position tree that still produces the correct write-set hash under implementation quirks specific to that subsystem), `ensure_match_transaction_info` will report success even though the position/hot-state root is wrong. This directly undermines the guarantee that "Authenticated API and proof-bearing responses must stay bound to the right ledger version, root, and object," since the replay/verification pipeline that is meant to catch exactly this kind of divergence is blind to it for these specific root fields.

### Likelihood Explanation
The gap is self-documented in the code as a known limitation ("must be fixed before we allow module updates" style TODO: "Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), confirming the maintainers are aware this check is incomplete but it has not yet been fixed at the time of this snapshot. The trading-native feature flags (`TRADING_NATIVE`, `NATIVE_POSITION`, `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) are wired through `Features`/`BlockExecutorConfigFromOnchain`, meaning the position-state root becomes a first-class, consensus-relevant field once these flags are enabled. [7](#0-6) 

Note: the primary online chunk-commit path (`execution/executor/src/chunk_executor/mod.rs::update_ledger` and `do_state_checkpoint.rs`) does separately validate `known_position_state_checkpoints`/`known_state_checkpoints` against the recomputed root during normal state-sync chunk application, so the live-node state-sync commit path is not blind to this. [8](#0-7) 
I was not able to fully confirm, within the remaining tool budget, whether the `TransactionReplayer`/backup-restore replay path (`storage/backup/backup-cli/.../restore.rs::replay_transactions`) relies exclusively on `ensure_match_transaction_info` or also goes through the same `DoStateCheckpoint`-based root-hash re-validation used by `update_ledger`. This is the key remaining uncertainty: if restore-replay's checkpoint/root validation is fully covered elsewhere (as it appears to be for the state-sync chunk path), the practical blast radius of this gap is limited to the standalone `db-tool replay_on_archive` / `aptos-debugger` / `cli` auditing tools, where the consequence is a missed detection in an offline integrity audit rather than an actual wrongly-committed mainnet ledger state.

### Recommendation
Extend `ensure_match_transaction_info` to also validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in the given `TransactionInfo`/recomputed state summary) against the actual recomputed roots, exactly as `DoStateCheckpoint::get_state_checkpoint_hashes` already does for the live chunk-commit path. This closes the gap flagged by the existing TODO and ensures all replay/restore/audit tools uniformly detect any state-root divergence, consistent with the requirement that proof-bearing artifacts remain bound to the correct ledger state.

### Proof of Concept
1. Enable `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (and prerequisite `TRANSACTION_INFO_V1`, `HOTNESS_IN_EPILOGUE`) so `TransactionInfoV1::position_state_checkpoint_hash` becomes a real, consensus-relevant field.
2. Run `storage/db-tool/src/replay_on_archive.rs` (or `aptos-debugger`/`cli replay`) against a backup/archive whose stored `TransactionInfo.position_state_checkpoint_hash` does not match what re-execution of the corresponding transactions would produce for the native-position tree (e.g., due to a divergent bug in position-tree computation, or a maliciously altered backup archive that swaps position-tree writes but leaves the aggregate write-set hash unaffected for the checked fields).
3. Observe `Verifier::execute_and_verify` -> `TransactionOutput::ensure_match_transaction_info` returns `Ok(())` because it only checks status, gas, write-set hash, and event root hash, and does not compare `position_state_checkpoint_hash`. [6](#0-5) [1](#0-0) 
4. The replay-verify tool reports a clean/successful verification even though the position-state root computed locally diverges from the authenticated one — a real hard-fork-class inconsistency goes undetected by the very tool designed to catch it.

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

**File:** types/src/block_executor/config.rs (L168-189)
```rust
    pub fn with_block_gas_limit_override(mut self, block_gas_limit_override: Option<u64>) -> Self {
        self.per_block_gas_limit = block_gas_limit_override;
        self
    }

    pub fn with_features(mut self, features: &Features) -> Self {
        self.hotness_in_epilogue = features.is_hotness_in_epilogue_enabled();
        self.transaction_info_v1 = features.is_transaction_info_v1_enabled();
        // Requires transaction_info_v1: the hot state root rides in
        // TransactionInfoV1's hot_state_checkpoint_hash field, which V0 lacks.
        self.hot_state_root_in_txn_info = features.is_hot_state_root_in_txn_info_enabled()
            && features.is_transaction_info_v1_enabled();
        // Requires transaction_info_v1 (the root rides in TransactionInfoV1) and
        // hotness_in_epilogue (only the V1 write-set format it enables serializes
        // the native-position extensions; V0 drops them, so output-replay would
        // diverge). Degrades to off if either is missing.
        self.compute_trading_native_state_roots = features
            .is_compute_trading_native_state_roots_enabled()
            && features.is_transaction_info_v1_enabled()
            && features.is_hotness_in_epilogue_enabled();
        self
    }
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
