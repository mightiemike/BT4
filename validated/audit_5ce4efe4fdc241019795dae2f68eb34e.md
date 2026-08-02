### Title
`TransactionOutput::ensure_match_transaction_info` silently skips checkpoint-hash verification, letting corrupted/diverged state roots pass replay-verification - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verification tooling to confirm that a locally re-executed `TransactionOutput` matches the authenticated, on-chain `TransactionInfo` for a given version. It checks status, gas used, the write-set hash (`state_change_hash`), and the event root hash, but it never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. The code contains its own acknowledgment of the gap.

### Finding Description
`ensure_match_transaction_info` in `types/src/transaction/mod.rs` performs these checks: [1](#0-0) 

and then explicitly states: [2](#0-1) 

The comment reads: "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution. Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS." [2](#0-1) 

`state_checkpoint_hash` is the root of the account/state Sparse Merkle Tree at a checkpoint version, and `position_state_checkpoint_hash` is the newly-added native-position state root that gets committed into `TransactionInfoV1` and is bound into the ledger accumulator when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled: [3](#0-2) [4](#0-3) 

This comparator is used by `replay_on_archive`, `aptos-debugger`, and `cli/src/commands.rs` (3 call sites) to validate that re-executed output matches the authenticated `TransactionInfo` fetched from storage/backup. Because the state/hot-state/position checkpoint hashes are excluded from the comparison, any of these tools can report "replay successful" even though the locally recomputed state root (main state tree or native-position tree) differs from the authenticated on-chain root — i.e., the tool fails to detect a divergence between what was actually committed to the ledger and what local execution independently computes.

### Impact Explanation
This breaks the proof/commitment-integrity guarantee that authenticated verification tooling is supposed to provide: replay-verification exists specifically to catch cases where committed ledger state (via the accumulator-bound `TransactionInfo`) diverges from correct VM/state-tree computation. With this comparator silently omitting checkpoint-hash checks, a state root corruption or divergence (e.g., in the native-position tree feeding `position_state_checkpoint_hash`, or the main state checkpoint tree) at a version already gated behind consensus/on-chain feature flags (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, `HOT_STATE_ROOT_IN_TXN_INFO`) would go undetected by the archive-replay and debugger verification paths, which are the primary defense-in-depth mechanism for catching such divergences post-commit. This falls within the state-commitment/proof-integrity gate: an authenticated response (the "replay succeeded" verdict from these tools) is bound to the wrong verification outcome relative to the true committed state.

### Likelihood Explanation
The gap is unconditional in the current code — it isn't behind a feature flag, so it is always present in this comparator whenever it is invoked, regardless of whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` are enabled. The likelihood of it mattering scales with adoption of the trading-native/hot-state features (currently these are described as off in testnet/mainnet per an adjacent comment), but the verification hole itself exists today in all call sites (`replay_on_archive`, `aptos-debugger`, `cli`).

### Recommendation
Extend `ensure_match_transaction_info` to also compare recomputed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present/expected) against the values carried in `txn_info`, as the existing TODO in the code already recommends, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled on any network where this tooling is relied upon for integrity verification.

### Proof of Concept
Not applicable as an exploit PoC — this is a verification-tooling gap rather than an exploitable transaction. Local proof of the broken invariant:
1. `ensure_match_transaction_info` compares `status`, `gas_used`, `state_change_hash` (write-set hash), and `event_root_hash` only. [1](#0-0) 
2. It never reads or compares `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` against any locally recomputed value. [2](#0-1) 
3. Since `TransactionInfoV1` carries `position_state_checkpoint_hash` as a field bound into the accumulator/consensus (once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is on), any local recomputation of the native-position tree that diverges from the authenticated value will pass `ensure_match_transaction_info` uncaught, and thus pass the `replay_on_archive`/`aptos-debugger`/`cli` replay-verify commands that call it.

I was unable to fully trace whether any additional caller (outside the three files found via `grep_search`) performs its own separate checkpoint-hash validation that would compensate for this gap; that would require reviewing each call site's surrounding logic in `aptos-move/cli/src/commands.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `storage/db-tool/src/replay_on_archive.rs` in full, which I did not have remaining iterations to do.

### Citations

**File:** types/src/transaction/mod.rs (L2148-2196)
```rust
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

**File:** types/src/on_chain_config/aptos_features.rs (L203-209)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
    /// When enabled, execution populates `TransactionInfoV1`'s hot state root hash, so it
    /// is committed to the ledger accumulator. Requires `TRANSACTION_INFO_V1`.
    HOT_STATE_ROOT_IN_TXN_INFO = 123,
```
