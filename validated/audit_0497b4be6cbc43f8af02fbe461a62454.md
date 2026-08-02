## Title
Replay-verification (`ensure_match_transaction_info`) does not validate state/hot-state/position checkpoint hashes, allowing silently-accepted divergent state roots - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the core comparator used by replay/debugging tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) to confirm that a locally re-executed transaction produced the same result as what was authenticated and committed to the ledger. It checks status, gas used, write-set hash, and event root hash, but explicitly skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` carried in `TransactionInfo`/`TransactionInfoV1`.

### Finding Description [1](#0-0) 

The function computes and checks only three invariants (status, gas, write-set hash, event root hash) between a freshly-computed `TransactionOutput` and the on-chain `TransactionInfo`. It never calls `TransactionInfo::state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` for comparison against locally-derived roots. This is acknowledged in-code: [2](#0-1) 

These checkpoint hashes are exactly the fields that bind the Sparse Merkle Tree state root (and, with `TRANSACTION_INFO_V1`/`HOT_STATE_ROOT_IN_TXN_INFO`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, the hot-state and native-position roots) into the transaction accumulator that consensus signs. They are populated during checkpoint construction in `execution/executor/src/workflow/do_state_checkpoint.rs` (`DoStateCheckpoint::run`, `get_state_checkpoint_hashes`, `compute_position_checkpoint`), and used to compute `TransactionInfoV1` fields such as `hot_state_checkpoint_hash` and `position_state_checkpoint_hash` (see `types/src/transaction/mod.rs` lines 2440-2494). The comment at the call site itself states this leaves `replay_on_archive` blind to divergence in the "authenticated position state root."

Consequently, if execution logic (or a malicious/corrupted local database used as the base state view) produces a state or position-tree root that differs from the one authenticated on mainnet, `ensure_match_transaction_info` will still report success as long as the write set (`state_change_hash`), events, gas and status match — a scenario that is entirely plausible for bugs specific to checkpoint/root computation (e.g. incorrect hot-state promotion logic, or bugs in `compute_position_checkpoint`) that don't touch the transaction's own write set.

### Impact Explanation
This breaks the "authenticated response bound to right root" invariant for the ledger's own replay/verification tooling: `db-tool replay-on-archive`, `aptos-debugger`, and `aptos move replay` all rely on `ensure_match_transaction_info` as their pass/fail signal for whether re-execution matches the immutable, signed ledger history. A root-computation bug (state root, hot-state root, or the newly introduced native-position root gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that diverges from the authenticated value at a given version would go undetected by this critical safety-net check, letting a hard-fork-class state-commitment bug silently pass "replay verified" status across the archive. This is a proof/commitment integrity gap directly in the tooling meant to catch exactly this class of bug.

### Likelihood Explanation
The gap is unconditional in current code (it's always skipped, not just under a flag), though its practical severity scales with adoption of `TRANSACTION_INFO_V1`/`HOT_STATE_ROOT_IN_TXN_INFO`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, which are being staged (feature flags exist, defaulted off) per `aptos-move/framework/move-stdlib/sources/configs/features.move` (lines 949-961) and `types/src/on_chain_config/aptos_features.rs`. The bug requires a separate root-computation defect to be masked, so it is a "second line of defense" failure rather than a standalone exploit, but it means any future regression in checkpoint/hot-state/position root computation would not be caught by replay-verify, undermining confidence in that tool exactly when it's most needed (post state-root feature rollout).

### Recommendation
Extend `ensure_match_transaction_info` to compare `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()` against locally recomputed values whenever the corresponding feature/config is active, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (and `HOT_STATE_ROOT_IN_TXN_INFO`) are enabled on mainnet, exactly as the existing TODO comment requests.

### Proof of Concept
Not applicable as a live exploit (this is a tooling/verification-gap finding, not a state-transition exploit): the gap is demonstrated directly by the code path — `storage/db-tool/src/replay_on_archive.rs` line 392 calls `ensure_match_transaction_info`, whose body (types/src/transaction/mod.rs 2139-2204) never reads the checkpoint-hash fields of `TransactionInfo`, so any test/replay scenario where a locally computed state/hot-state/position root differs from the archived `TransactionInfo`'s corresponding hash — while write set, events, gas, and status remain identical — will pass verification without error.

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
