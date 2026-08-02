## Finding

The genuine, locally-provable analog to the reported "pending debt doesn't accrue interest" bug-class (a value that quietly diverges from the correct/complete state but passes as validated because a check omits part of the committed data) is in `TransactionOutput::ensure_match_transaction_info` in [1](#0-0)  .

### Title
Chunk/replay and debugger integrity check silently skips checkpoint-hash validation, allowing divergent trading-native state to be accepted as a verified replay - (File: types/src/transaction/mod.rs)

### Summary
`ensure_match_transaction_info` is the function used by chunk execution, the replay/verify tool, the CLI, and the Aptos debugger to assert that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` recorded on-chain/on-disk. It checks status, gas, write-set hash (`state_change_hash`), and event root hash, but — per the function's own `TODO(trading-native)` comment — it does **not** check `state_checkpoint_hash`, the hot-state checkpoint hash, or `position_state_checkpoint_hash` [2](#0-1) .

### Finding Description
`TransactionInfo` carries multiple committed roots that authenticate different parts of ledger state: `state_change_hash` (write-set hash), `event_root_hash`, and `state_checkpoint_hash` (and, per the comment, an analogous hash for the new "trading-native" position/hot-state feature gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) [3](#0-2) . `ensure_match_transaction_info` is the single sanity gate used by:
- `execution/executor/src/chunk_executor/mod.rs` (chunk apply/verify path),
- `storage/db-tool/src/replay_on_archive.rs` (replay-verify tooling),
- `aptos-move/aptos-debugger/src/aptos_debugger.rs`,
- `aptos-move/cli/src/commands.rs`.

Because the function only compares `write_set_hash` against `state_change_hash` and `event_root_hash` against the event accumulator root, any divergence that only shows up in the state-checkpoint / position-state root (e.g., a Jellyfish Merkle state-checkpoint hash mismatch, or the new position-state Merkle root introduced for trading-native features) is never detected by this call. The comment explicitly states the consequence: replay-verify tooling can report a successful replay "even when the authenticated position state root diverges from local execution" [4](#0-3) .

This matches the report's bug class directly: just as pending debt sits in `DefaultPool` without accruing interest because it's "not yet allocated" and thus skipped from a check that should otherwise cover it, here the position/hot-state checkpoint commitment is "not yet validated" and thus skipped from an integrity check that is supposed to cover the whole `TransactionOutput`/`TransactionInfo` correspondence.

### Impact Explanation
If `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any code path relying on the state/hot-state checkpoint hash for correctness) is enabled while this comparator remains unpatched, a node performing chunk execution, replay-verify, or debugger-based state reconstruction can accept and persist/report a `TransactionOutput` whose position/hot-state root does not match the authenticated ledger value, without raising the `ensure!` error. This is a state-commitment/proof-integrity gap: it allows corrupted or diverged durable state to pass verification undetected in restore/replay flows, which is explicitly in-scope under "Committed state that differs from the correct VM result... during commit, replay, restore, or proof verification."

### Likelihood Explanation
The likelihood is bounded by the gating feature flag: the comment says this must be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`," implying the feature is not (or not fully) active yet, so today the gap is latent rather than actively exploitable on mainnet. I could not fully verify from the index whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is already active in any live path (the feature flag file and `aptosdb_reader.rs`/`aptosdb_writer.rs` references show it gates behavior, but I was unable to load full file contents for `aptosdb_reader.rs`/`chunk_executor/mod.rs` due to index/content limits in this session). This is a known, developer-acknowledged TODO rather than a hidden defect, which lowers novelty but does not eliminate the risk that a partially-enabled feature (position/hot-state checkpointing code already exists in `execution/executor-types/src/state_checkpoint_output.rs` and `execution/executor/src/workflow/do_state_checkpoint.rs`) is live while the corresponding verification is still absent.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `self`'s computed state-checkpoint hash (and, when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, the position/hot-state checkpoint hash) against `txn_info.state_checkpoint_hash()`/the corresponding field, failing the `ensure!` on mismatch just as is done for `state_change_hash` and `event_root_hash`. Do not enable `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in any environment (including staged rollouts) until this check is completed, and add regression tests in `replay_on_archive` verifying that a corrupted position-state root is rejected.

### Proof of Concept
Not independently constructible from static analysis alone: exploiting this requires (a) `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or equivalent position/hot-state checkpointing to be active, and (b) a code path that produces a `TransactionOutput` whose position-state checkpoint diverges from the recorded `TransactionInfo`. I was unable to confirm from indexed contents whether such an active/reachable path exists on mainnet today (the relevant files `storage/aptosdb/src/db/aptosdb_reader.rs` and `execution/executor/src/chunk_executor/mod.rs` returned empty/truncated content in this session, likely due to index size limits). The strongest concrete evidence is the developer-authored TODO comment itself, which documents the exact missing invariant and its consequence [4](#0-3) .

**Caveat**: Due to index size limits, I could not retrieve the full contents of `storage/aptosdb/src/db/aptosdb_reader.rs`, `storage/aptosdb/src/db/aptosdb_writer.rs`'s `COMPUTE_TRADING_NATIVE_STATE_ROOTS` usage, or `execution/executor/src/chunk_executor/mod.rs` to confirm the current activation state of this feature. If you need a definitive determination of whether this gap is live on mainnet today (vs. purely latent/future-gated), I'd recommend starting a full Devin session with repository access to inspect those files directly.

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

**File:** types/src/on_chain_config/aptos_features.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```
