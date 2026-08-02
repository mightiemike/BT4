This is a strong, well-documented lead. Let me verify where `ensure_match_transaction_info` is used to confirm impact.### Title
`ensure_match_transaction_info` skips checkpoint-hash validation, allowing divergent state/hot-state/position roots to pass replay and chunk-verification checks - (`types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` (`types/src/transaction/mod.rs` lines 2139-2204) is the authenticated-comparison routine used to verify that a locally-computed `TransactionOutput` matches an on-chain `TransactionInfo`. It checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but the code itself documents that it deliberately omits verification of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`: [1](#0-0) 

### Finding Description
`TransactionInfo` carries multiple root-hash fields beyond the write-set/event hashes: `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (see the `TransactionInfoV1::builder_v1` fields) [2](#0-1) . These fields commit to the state Merkle root, hot-state root, and the new "position state" (trading-native) root at that version — i.e., they are the authenticated proof-binding fields tying a transaction's output to a specific ledger state root.

`ensure_match_transaction_info` is the function multiple call sites use to authenticate that a computed/replayed `TransactionOutput` corresponds to the `TransactionInfo` supplied by a peer or read from storage:
- `execution/executor/src/chunk_executor/mod.rs`
- `storage/db-tool/src/replay_on_archive.rs`
- `aptos-move/aptos-debugger/src/aptos_debugger.rs`
- `aptos-move/cli/src/commands.rs` [3](#0-2) 

Its checks explicitly stop at `write_set_hash` (state_change_hash) and `event_root_hash`; it never dereferences `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`. The comment inline confirms this is a known, intentional gap: [4](#0-3) 

Because the write-set hash alone does not commit to the JMT/hot-state/position-state root (those roots are computed separately during `do_state_checkpoint.rs`'s checkpoint-hash derivation, e.g. `get_state_checkpoint_hashes` in `execution/executor/src/workflow/do_state_checkpoint.rs`), a replay or chunk-execution path that only calls `ensure_match_transaction_info` can accept a `TransactionOutput`/`TransactionInfo` pair whose committed **state root diverges** from what local re-execution actually produced, as long as the write set and events happen to match. This is precisely the "committed state that differs from the correct VM result" and "wrong ... state proof accepted as valid" class called out by the State-Integrity Gate.

### Impact Explanation
If a replay/verification tool (e.g., `db-tool replay-verify` via `replay_on_archive.rs`, or debugger-based replay in `aptos-debugger.rs`/`cli/src/commands.rs`) relies on `ensure_match_transaction_info` as its correctness oracle, it can report a transaction as "matching" even when the state/hot-state/position-state checkpoint root it locally computed diverges from the authenticated on-chain value. This directly undermines the guarantee that "authenticated API or state-view output [stays] bound to the right ledger version, root, and object," since divergence in the state Merkle root (the actual state-commitment artifact) is silently ignored by the one function whose job is to catch exactly that divergence. In a replay-verification context this can mask a genuine consensus/execution non-determinism or storage corruption bug (hard-fork-class divergence) that would otherwise be caught before it propagates.

### Likelihood Explanation
This is not a hypothetical edge case — the gap is self-documented in the code with a named follow-up feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`), confirming the authors are aware the comparator is incomplete precisely in the position/hot-state root dimension. Any code path that uses `ensure_match_transaction_info` as its sole match check (rather than independently comparing computed checkpoint hashes against `TransactionInfo`'s checkpoint fields) inherits this blind spot without any additional trigger conditions required.

### Recommendation
Extend `ensure_match_transaction_info` (or its callers) to compare the locally computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against `txn_info`'s corresponding fields whenever those hashes are available/computable, rather than deferring this to a future flag. At minimum, callers in `chunk_executor/mod.rs` and `replay_on_archive.rs` that treat a passing `ensure_match_transaction_info` as full verification should be audited to confirm they perform an independent checkpoint-hash comparison elsewhere; if not, this represents a real gap in the replay/verify correctness guarantee.

### Proof of Concept
Not independently reproducible from the indexed code alone — I was not able to fully trace whether `chunk_executor/mod.rs`'s state-sync/replay verification path (`ChunkResultVerifier`/`StateSyncChunkVerifier`) performs a separate, independent check of `state_checkpoint_hash` outside of `ensure_match_transaction_info` before accepting a chunk. If such a separate check exists (e.g., via `DoStateCheckpoint::get_state_checkpoint_hashes` being compared against `known_state_checkpoints` derived from the `TransactionInfo` list), the actual commit path may be protected and only the standalone debugger/CLI/replay-on-archive tooling would be exposed. This distinction could not be fully confirmed within the available tool budget and index depth, so likelihood should be treated as tool/path-dependent until the `ChunkResultVerifier` implementations (`chunk_result_verifier.rs`) are reviewed directly with full source access.

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

**File:** types/src/transaction/mod.rs (L2196-2204)
```rust

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
