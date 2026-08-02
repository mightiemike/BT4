## Summary
This search reduces the external "expiry defaults to the wrong permission state" bug class to its Aptos-native analog: **a verification routine that is supposed to bind a locally-computed result to an authenticated on-chain commitment, but silently skips checking a subset of the fields it claims to validate**. That is exactly what `TransactionOutput::ensure_match_transaction_info` does for the new (`TransactionInfoV1`) checkpoint-hash fields.

### Title
`ensure_match_transaction_info` skips verifying state/hot-state/position checkpoint hashes, letting replay/restore accept a diverged authenticated root - (File: `types/src/transaction/mod.rs`)

### Finding Description
`TransactionOutput::ensure_match_transaction_info` is the function used to assert that a locally-recomputed `TransactionOutput` is consistent with the authenticated `TransactionInfo` fetched/stored from the ledger (accumulator leaf). It checks status, gas used, `state_change_hash` (write-set hash), and `event_root_hash`, but explicitly **does not check** `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the very fields that commit the Jellyfish Merkle / hot-state / native-position roots into `TransactionInfoV1`: [1](#0-0) 

The code even documents the gap itself: [2](#0-1) 

This function is called from ledger-integrity-critical paths: the chunk executor (`execution/executor/src/chunk_executor/mod.rs`), the archive replay-verify tool (`storage/db-tool/src/replay_on_archive.rs`), the Aptos debugger, and the CLI (`aptos-move/cli/src/commands.rs`), all of which rely on it to detect divergence between locally executed results and the authenticated `TransactionInfo` pulled from storage/backup.

`TransactionInfoV1`'s `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` fields exist specifically because they are consensus-verified/committed into the accumulator once `TRANSACTION_INFO_V1`, `HOT_STATE_ROOT_IN_TXN_INFO`, and `COMPUTE_TRADING_NATIVE_STATE_ROOTS` are enabled: [3](#0-2) 

Because `ensure_match_transaction_info` never compares these hashes, a divergence in the locally-computed state/hot-state/native-position root (e.g. from an executor bug, corrupted local state tree, or a maliciously crafted archive) will not be caught by this verification path, even though the function's name and purpose imply full parity checking between computed output and authenticated ledger data.

### Impact Explanation
This breaks the proof/storage invariant that "VM outputs, transaction infos, events, and write sets must survive executor-to-storage handoff unchanged" and that "authenticated API and proof-bearing responses must stay bound to the right ledger version, root, and object." Concretely:
- `replay_on_archive` (used to validate archived/backed-up ledger history) and the chunk executor's commit-time verification can both report a **successful replay/commit** even when the recomputed state-checkpoint root (JMT root), hot-state root, or native-position root diverges from the authenticated `TransactionInfoV1` committed on-chain.
- This is a hard-fork-relevant divergence class: nodes that trust this check as their sole discrepancy detector could accept/propagate a state tree that doesn't match the consensus-committed root without any alarm, undermining the guarantee that Merkle proofs served against that state are actually bound to the correct root.

### Likelihood Explanation
The gap is unconditional whenever `TransactionInfoV1` and the new checkpoint-root features are active (which is the direction the codebase is moving, per the `HOTNESS_IN_EPILOGUE`/`TRANSACTION_INFO_V1`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flags). It doesn't require an attacker at all to be a real defect — any real bug in position/hot-state root computation, or a corrupted/malicious backup archive with a subtly wrong checkpoint hash, would slip through the exact verification function designed to catch it. The severity is amplified because the code comment itself already flags this as unaddressed ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"), meaning this is a live, acknowledged-but-unfixed gap rather than a hypothetical one.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally recomputed values whenever `txn_info` is a `TransactionInfoV1` (feature-gated), before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` in production, so replay-verify and chunk-executor commit paths cannot silently pass when these roots diverge.

### Proof of Concept
Not independently reproducible from static analysis alone — this is a logic-gap finding (missing checks), not an exploitable code path with concrete inputs. Confirming exploitability requires constructing a `TransactionOutput`/`TransactionInfoV1` pair with matching `state_change_hash`/`event_root_hash` but mismatched `state_checkpoint_hash` (or hot-state/position hash) and observing that `ensure_match_transaction_info` returns `Ok(())`, then tracing through `replay_on_archive.rs` and `chunk_executor/mod.rs` to confirm no other check independently catches the same mismatch. I was unable to fully verify whether an independent redundant check exists elsewhere in `chunk_executor/mod.rs` or `replay_on_archive.rs` in the time available — this should be validated with a live Devin session that can read those two call sites in full and run/construct a targeted unit test.

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

**File:** aptos-move/framework/move-stdlib/sources/configs/features.move (L949-961)
```text
    /// When enabled, execution computes the trading-native state roots and commits them to
    /// `TransactionInfoV1`, so they are consensus-verified. Requires `TRANSACTION_INFO_V1`.
    /// Covers the native-position tree today and is intended to cover the other trading-native
    /// trees as they are added. Enabling it first commits the (empty-tree) roots to transaction
    /// info; the actual Move-side writes to those trees are gated by separate flags.
    /// Lifetime: permanent
    const COMPUTE_TRADING_NATIVE_STATE_ROOTS: u64 = 122;

    /// When enabled together with `TRANSACTION_INFO_V1`, execution populates
    /// `TransactionInfoV1`'s hot state root hash, so it is committed to the ledger
    /// accumulator. Requires `TRANSACTION_INFO_V1`.
    /// Lifetime: permanent
    const HOT_STATE_ROOT_IN_TXN_INFO: u64 = 123;
```
