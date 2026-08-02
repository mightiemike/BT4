### Title
`TransactionOutput::ensure_match_transaction_info` skips state-checkpoint hash validation, allowing replay-verify to accept a diverging state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verify tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) to check that a freshly re-executed `TransactionOutput` matches the authenticated `TransactionInfo` pulled from an archive/backup. It checks status, gas, write-set hash (`state_change_hash`) and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, with an inline TODO acknowledging the gap.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates:
- execution status,
- gas used,
- write-set hash vs `txn_info.state_change_hash()`,
- event root hash vs `txn_info.event_root_hash()`.

It then returns `Ok(())` immediately with this comment: [2](#0-1)  stating that the checkpoint hashes (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`) are intentionally *not* validated, and that `db-tool`'s `replay_on_archive` tooling can therefore report a "successful replay" even when the authenticated position/state root diverges from local execution. This mirrors the report's root cause pattern: a value meant to *protect* the integrity check (the checkpoint root) is either not compared at all or effectively compared to itself in a way that provides no real protection.

The state checkpoint hash is exactly the field that commits the resulting Sparse-Merkle/Jellyfish-Merkle world-state root for a block/checkpoint transaction — it is the strongest state-commitment field in `TransactionInfo` (stronger than the per-transaction write-set hash, which only covers that one transaction's deltas, not the full resulting state tree). Skipping it means the one check capable of catching a genuinely different world state (e.g., from a divergent VM execution, storage bug, or upgrade regression affecting `hot_state` / `position` state trees) is bypassed by the very tool whose job is to detect exactly that divergence.

### Impact Explanation
Replay-verify (`replay_on_archive`, `aptos-debugger`, and the CLI's replay path) is the primary safety net used to detect state-divergence bugs before/after upgrades by re-executing historical transactions against a state and comparing to the already-committed, signature-authenticated `TransactionInfo`. Because `ensure_match_transaction_info` does not compare `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, a hard-fork-causing state divergence localized to the checkpoint-only fields (state root, hot-state root, or the new "trading-native" position state root gated by `compute_trading_native_state_roots`) would not be caught by this verification path, even though write-set and event hashes for individual transactions still matched. This directly falls in the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Wrong ... state proof accepted as valid" categories in scope, since a state root that diverges from the correct VM result is accepted as consistent.

### Likelihood Explanation
This is not a hypothetical: the code explicitly flags the gap via a TODO comment tied to enabling the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` on-chain config flag (`types/src/block_executor/config.rs`, `on_chain_config/aptos_features.rs`), meaning the feature that introduces `position_state_checkpoint_hash` is on a path toward being enabled, and the developers themselves flagged that the checkpoint-hash comparator must be fixed "before enabling" it. Until that fix lands, any replay-verify run performed with this checker (used to guard mainnet upgrades) is a false sense of security for exactly the class of bug that matters most: silent state-root divergence. Likelihood of the check being exercised is high (replay-verify is part of the standard pre/post upgrade CI process — see `testsuite/replay-verify/main.py`), but the actual under-detection only manifests if there's a real state-tree divergence bug elsewhere, so likelihood of *exploitation* impact depends on a second bug existing; on its own, this is a monitoring/detection gap rather than a directly triggerable state corruption.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in the given/expected `TransactionInfo`) against a checkpoint hash independently recomputed by the caller from the post-execution state view, mirroring the pattern already used for `state_change_hash`/`event_root_hash`. This should be completed and enabled before turning on `compute_trading_native_state_roots` in production onchain config, since that is precisely the scenario the existing TODO warns about.

### Proof of Concept
1. Take a version range where a checkpoint transaction's resulting state (or hot-state, or position-state) root differs between the archived `TransactionInfo.state_checkpoint_hash` and what local re-execution would actually produce (e.g., due to a storage/committing bug introduced elsewhere, or a bug gated behind `compute_trading_native_state_roots`), while per-transaction write sets and events for the individual transactions still hash identically.
2. Run `storage/db-tool/src/replay_on_archive.rs`, which calls `ensure_match_transaction_info` — see call site referenced in `storage/db-tool/src/replay_on_archive.rs`.
3. Because `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` are never compared (per [2](#0-1) ), the function returns `Ok(())` and reports the replay as successful, even though the state root has diverged from the authenticated ledger.

Note: I was unable to fully trace whether `compute_trading_native_state_roots` / `position_state_checkpoint_hash` is currently enabled by default on mainnet (index coverage of `types/src/on_chain_config/aptos_features.rs` and `types/src/block_executor/config.rs` was only partially inspected due to remaining-iteration limits), so the immediate exploitability depends on that flag's current rollout status — this should be confirmed in a follow-up session with full repository access.

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
