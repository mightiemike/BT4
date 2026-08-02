### Title
`ensure_match_transaction_info` skips validating `state_checkpoint_hash` / `position_state_checkpoint_hash`, letting replay-verify tooling accept a corrupted committed state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-output-vs-`TransactionInfo` consistency check used by replay/verification tooling. It checks status, gas, write-set hash, and event root hash against the values embedded in `TransactionInfo`, but explicitly (and by an in-code TODO admission) never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This leaves the state-commitment root — the very value that authenticates the world state / native-position tree at a version — outside the local re-derivation check that replay tooling relies on.

### Finding Description
`ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) is meant to prove that a locally re-executed `TransactionOutput` matches the `TransactionInfo` that was actually committed to the accumulator/ledger. It is invoked from `storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs` — code paths whose entire purpose is to detect divergence between local execution and the committed chain state (mainnet-replay verification, debugging incidents, and CLI-driven simulation/verification of historical state).

The function validates:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set_hash` (`CryptoHash::hash(self.write_set())`) vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

It does **not** validate:
- `txn_info.state_checkpoint_hash()` (the Sparse Merkle root of world state after the transaction/checkpoint)
- `txn_info.hot_state_checkpoint_hash()`
- `txn_info.position_state_checkpoint_hash()` (the native-position JMT root, gated by feature `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, see `types/src/on_chain_config/aptos_features.rs:203-206`)

The code contains an explicit admission of this gap:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [1](#0-0) 

This matters because `state_checkpoint_hash` and `position_state_checkpoint_hash` are themselves committed inside `TransactionInfo`, which is what the transaction accumulator authenticates via `TransactionAccumulatorProof::verify` (`types/src/proof/definition.rs:66-111`) and what `TransactionInfoWithProof::verify` checks against a `LedgerInfo` (`types/src/proof/mod.rs:39-61`). Those accumulator/ledger-info proofs only prove that a given `TransactionInfo` byte blob is at a given version/root — they say nothing about whether that `TransactionInfo`'s embedded state root actually corresponds to correct VM execution. That correctness check is supposed to happen locally by re-executing and calling `ensure_match_transaction_info`; since the state-root fields are skipped, a `TransactionInfo` carrying an incorrect `state_checkpoint_hash` or `position_state_checkpoint_hash` — e.g. produced by a bug in checkpoint/root computation such as the dual on/off-chain-flag computation path in `storage/aptosdb/src/db/aptosdb_writer.rs:402-477` (`position_summary_at_commit`, used only when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is off but still capable of diverging from the execution-computed summary when the flag toggles or under replay/restore edge cases) — would pass replay verification undetected.

### Impact Explanation
This breaks the "proof and storage pivot" invariant that authenticated ledger objects (here, `TransactionInfo`, which is the leaf object of the transaction accumulator and the vehicle carrying the state/position-state roots) must be independently verifiable against locally re-derived execution results. Once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled (a currently-planned rollout per the code comments), a wrong `position_state_checkpoint_hash` — from a storage bug, a divergent replica, or any other source of state corruption — would not be flagged by `replay_on_archive`, the debugger, or the CLI verification commands, all of which rely on `ensure_match_transaction_info` as their correctness oracle. This is exactly the kind of "committed state that differs from the correct VM result... accepted as valid" scenario the state-integrity gate targets, since the primary tool meant to catch such divergence silently passes.

### Likelihood Explanation
Today this is latent because `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is not yet enabled on mainnet (the code says "before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"), and `state_checkpoint_hash`/`hot_state_checkpoint_hash` validation was seemingly never added even for the pre-existing (long-shipped) main-state checkpoint hash, meaning any regression producing a wrong `state_checkpoint_hash` would similarly slip past replay-verify tooling today. The gap is unprivileged — it requires no attacker action, only a legitimate future feature activation or an unrelated bug elsewhere in checkpoint-hash computation — and it is 100% reproducible by inspection of the comparator's field list.

### Recommendation
Extend `ensure_match_transaction_info` to also compare locally-recomputed `state_checkpoint_hash` (and, once available, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against the corresponding fields of `txn_info`, gated appropriately on whether a checkpoint occurred at that version. Do this before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or `HOT_STATE_ROOT_IN_TXN_INFO` are turned on, and treat the current gap for `state_checkpoint_hash` (main-state root) as a pre-existing correctness hole in replay-verify tooling that should be fixed regardless of the trading-native feature.

### Proof of Concept
1. Enable (or simulate a future rollout of) `COMPUTE_TRADING_NATIVE_STATE_ROOTS` so `TransactionInfoV1.position_state_checkpoint_hash` is populated and consensus-verified.
2. Cause the persisted `position_state_checkpoint_hash` committed in `TransactionInfo` to diverge from what fresh local execution would produce (e.g. via a bug/regression in `position_summary_at_commit` in `storage/aptosdb/src/db/aptosdb_writer.rs:402-477`, or any storage-level corruption of the position JMT root).
3. Run `aptos-move/db-tool`'s `replay_on_archive` (or the debugger / CLI paths at `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs`) against the affected version range.
4. Because `ensure_match_transaction_info` never compares `position_state_checkpoint_hash` (nor `state_checkpoint_hash`), the tool reports a successful replay/verification despite the state root mismatch, exactly as documented by the in-code TODO. [2](#0-1) [3](#0-2)

### Citations

**File:** types/src/transaction/mod.rs (L2159-2204)
```rust
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

**File:** storage/db-tool/src/replay_on_archive.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```
