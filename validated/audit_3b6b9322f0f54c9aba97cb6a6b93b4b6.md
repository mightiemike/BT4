### Title
`TransactionOutput::ensure_match_transaction_info` skips state-checkpoint hash validation, letting replay/verify accept a corrupted state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticity gate used by replay/verify tooling (`replay_on_archive`, `aptos-debugger`, chunk executor's txn-info comparison) to confirm that a locally-recomputed `TransactionOutput` matches the `TransactionInfo` that was actually committed/signed into the ledger. It checks `status`, `gas_used`, the write-set hash (`state_change_hash`) and the event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the very fields that summarize the Merkle root of world state after the transaction.

### Finding Description
`TransactionInfo` carries several distinct commitments (`types/src/transaction/mod.rs:2402-2415`, `2448-2456`):
- `state_change_hash` – hash of the write set (checked).
- `event_root_hash` – hash of emitted events (checked).
- `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` – Sparse-Merkle-Tree roots of the resulting world state (NOT checked).

`ensure_match_transaction_info` (types/src/transaction/mod.rs:2139-2204) validates only status, gas, write-set hash, and event root hash: [1](#0-0) 
and the function's own comment admits the gap: [2](#0-1) 

This means a `TransactionOutput` whose *write set* correctly hashes to the expected `state_change_hash` (i.e., the raw list of key/value writes matches) can still, once applied to the state tree, produce a **different root hash** than the one recorded in the persisted/authenticated `TransactionInfo` — for example if the state-tree update logic (Jellyfish Merkle Tree update, hot-state promotion, or the newer `position_state` trading-native tree) diverges due to a bug in `DoStateCheckpoint` (execution/executor/src/workflow/do_state_checkpoint.rs) or in how a shard/side-tree is folded into the checkpoint. Because `ensure_match_transaction_info` is the single generic comparator used by both online chunk execution flows and by `replay_on_archive`/debugger replay-verification tooling, none of these callers independently re-validate the state-checkpoint hash before declaring "replay succeeded."

This is the same *bug class* as the Taiko finding: a downstream verification/finalization step trusts an intermediate signal (validity of the write set / a bond posting) while silently discarding a piece of history (the actual resulting state root / the true correctness of the challenged party) that should have gated the final outcome. Here, the "final verified transition" (the committed `TransactionInfo.state_checkpoint_hash`) is not cross-checked against the recomputed root, so a state root that diverges from the authenticated one is not caught by this generic invariant check.

### Impact Explanation
If `DoStateCheckpoint::run` (execution/executor/src/workflow/do_state_checkpoint.rs:36-84) or any state-tree update path produces a root hash that differs from the one embedded in the already-committed `TransactionInfo` (e.g. due to a bug in extending `LedgerStateSummary`, in the hot-state or `position_state` root folding, or a future regression), `ensure_match_transaction_info` — the shared authenticity/consistency check — will not flag the mismatch as long as the write set and event hashes still match. Tooling built on top of this comparator (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) can therefore report a "successful replay" for a chunk whose derived state root diverges from the authenticated ledger state — i.e., a silent corruption of the durable state-commitment invariant that replay/verify is supposed to catch. This directly matches the "wrong accumulator/state root accepted as valid" and "authenticated API/verification output bound to the wrong ledger state" impact classes.

### Likelihood Explanation
This is not exploitable by an external unprivileged network attacker to directly corrupt consensus (that path is gated by full BFT execution and state-checkpoint hash validation is enforced elsewhere in `DoStateCheckpoint::get_state_checkpoint_hashes` for on-chain replication, per code comments at types/src/transaction/mod.rs:2197-2203). The exposure is specifically in the **replay-verification/debugging tooling** path that reuses `ensure_match_transaction_info` as its correctness oracle: a hard-fork-only or feature-flag-only divergence (e.g. as `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / `position_state` matures) in state-root computation would go undetected by this specific comparator, even though the code's own comment flags this as a known gap ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"). Likelihood of the underlying state-tree divergence occurring is low today (the feature is not yet fully enabled), but the detection/verification invariant itself is provably incomplete right now.

### Recommendation
Extend `ensure_match_transaction_info` to also compare the recomputed `state_checkpoint_hash` (and, where applicable, `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) against `txn_info`'s values whenever those hashes are expected (e.g., at checkpoint boundaries), rather than relying solely on write-set/event hash equality. This closes the gap before `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/native trading-state roots are enabled, ensuring replay/verify tooling cannot silently accept a corrupted or divergent state root.

### Proof of Concept
No standalone runnable PoC was constructed; the finding is derived directly from local code inspection:
1. `TransactionInfo` stores `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` as separate commitments from `state_change_hash` (types/src/transaction/mod.rs:2402-2456).
2. `TransactionOutput::ensure_match_transaction_info` only asserts equality of `status`, `gas_used`, write-set hash, and event root hash (types/src/transaction/mod.rs:2139-2196), and its own trailing comment (2197-2203) explicitly documents that checkpoint-hash comparison is missing and that `replay_on_archive` can report success despite a diverging authenticated position state root.
3. This function is the shared oracle invoked by chunk-executor and by external replay-verify/debugger tooling (`execution/executor/src/chunk_executor/mod.rs`, `storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`), so the gap propagates to all of them without further validation of state-checkpoint hashes.

A full working exploit would require constructing/triggering a state-tree update bug that preserves write-set/event hashes but changes the checkpoint root — this was not exercised end-to-end in this investigation; the above therefore demonstrates the broken/missing invariant in the verification function itself rather than a fully realized exploited-in-the-wild state corruption.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2178)
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
