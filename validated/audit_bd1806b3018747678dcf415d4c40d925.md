## Title
`ensure_match_transaction_info` skips validating the trading-native position state root, letting replay-verify accept a divergent committed ledger state - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity gate used by replay/verification tooling (`storage/db-tool/src/replay_on_archive.rs`, `execution/executor/src/chunk_executor/mod.rs`) to confirm that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` committed to the ledger accumulator. It checks status, gas, write-set hash, and event root hash, but explicitly does **not** check the `position_state_checkpoint_hash` (nor the hot-state checkpoint hash) that is embedded in `TransactionInfoV1` when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled. The code contains a TODO acknowledging this exact gap.

### Finding Description
`TransactionInfoV1`/checkpoint machinery computes a `position_state_checkpoint_hash` representing the root of the native-position Jellyfish Merkle tree (see `execution/executor-types/src/state_checkpoint_output.rs`, `storage/aptosdb/src/db/aptosdb_writer.rs::commit_native_position`/`position_summary_at_commit`, and the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature description in `aptos-move/framework/move-stdlib/sources/configs/features.move`). This hash is intended to be consensus-verified as part of the committed `TransactionInfo`, analogous to `state_change_hash` and `event_root_hash`.

However, `ensure_match_transaction_info`: [1](#0-0) 
only validates `status`, `gas_used`, `write_set_hash` (against `state_change_hash`), and `event_root_hash`. It explicitly returns `Ok(())` without comparing `txn_info.position_state_checkpoint_hash()` (or `hot_state_checkpoint_hash()`) against the locally computed value, with an inline comment stating:

"this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution. Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS."

This function is invoked from `storage/db-tool/src/replay_on_archive.rs` (the tool operators use to confirm that replaying archived transactions reproduces the exact ledger state committed on-chain) and from `execution/executor/src/chunk_executor/mod.rs`. Because the position-state root is excluded from the comparison, a local execution can produce a native-position Merkle root that differs from the one actually committed to the ledger (accumulator-bound `TransactionInfo`), while `ensure_match_transaction_info` still reports success.

### Impact Explanation
This breaks the "authenticated API / proof-bearing responses must stay bound to the right ledger version, root, and object" and "restore/replay paths must not reinterpret committed data into a different ledger state" invariants. Concretely:
- `replay_on_archive` (the mainnet-facing replay/verification tool operators and auditors rely on to detect divergence between VM execution and committed history) will silently pass even if the native-position state tree diverges from the authenticated `TransactionInfoV1.position_state_checkpoint_hash`. Divergence here could stem from an executor bug, a JMT extend bug in `position_summary_at_commit`, or a hard-fork-only behavior change in position-tree computation — none of which would be caught.
- Any downstream consumer that trusts "replay-verify passed" as validating full ledger-state equivalence (including position state, which is proof-bearing and can be queried via `get_position_state_proof_by_version_ext`) would be operating on an unverified assumption, allowing a wrong committed/re-derived state root to go undetected.

This matches the required "wrong accumulator root, Merkle proof ... accepted as valid" and "committed state that differs from the correct VM result" impact categories once `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`TRANSACTION_INFO_V1` are enabled and native-position state becomes an authenticated part of the ledger.

### Likelihood Explanation
This is not an attacker-triggerable exploit by itself — it is a missing verification check, not a broken permission gate. Its severity is conditional: the feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) must be enabled and consensus must commit `position_state_checkpoint_hash` into `TransactionInfoV1` for this gap to matter. The code itself documents the gap as a known TODO ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"), indicating the feature is not yet fully wired for verification safety. I could not confirm from available context whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is already enabled on any live network — this is a **known unresolved unknown**; if it is not yet enabled on mainnet, actual impact is currently latent/pre-mainnet rather than live.

### Recommendation
Extend `ensure_match_transaction_info` to compare the locally computed position/hot-state checkpoint hashes (when `TransactionInfoV1` carries them, i.e., when `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` are active) against `txn_info.position_state_checkpoint_hash()` / `txn_info.hot_state_checkpoint_hash()`, erroring out on mismatch exactly as done for `state_change_hash` and `event_root_hash`. This check must be added and enabled *before* `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is turned on in any environment where replay verification is relied upon for integrity guarantees.

### Proof of Concept
No exploit PoC is provided because this is a code-level omission (not an externally triggerable transaction sequence) confirmed directly from the source and its own inline TODO comment: [2](#0-1) 
Reproduction would require: (1) enabling `TRANSACTION_INFO_V1` and `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, (2) constructing an executor/storage divergence in the native-position tree (e.g., via a bug in `position_summary_at_commit` in `storage/aptosdb/src/db/aptosdb_writer.rs`), and (3) running `replay_on_archive` to observe it report success despite the mismatched position root — this last step could not be executed in this read-only analysis environment.

### Citations

**File:** types/src/transaction/mod.rs (L2168-2203)
```rust
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
```
