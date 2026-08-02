## Title
`TransactionOutput::ensure_match_transaction_info` skips checkpoint-root verification, letting corrupted state/hot-state/position roots pass replay and chunk-executor validation - (`types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the invariant check used by the chunk executor and replay/verify tooling to confirm that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` pulled from a proven `TransactionInfoWithProof`/backup/on-chain source before it is accepted into storage. It checks status, gas used, the write-set hash (`state_change_hash`) and the event root hash, but it explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents but has not fixed.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  validates a `TransactionOutput` against a trusted `TransactionInfo` by comparing status, `gas_used`, `write_set_hash` vs `state_change_hash`, and `event_root_hash`. It stops there, and the code has an explicit TODO acknowledging the omission: [2](#0-1) 

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
Ok(())
```

This function is used as the integrity gate in the chunk executor commit path (`execution/executor/src/chunk_executor/mod.rs`) and in `storage/db-tool/src/replay_on_archive.rs`, both of which rely on it to detect divergence between local re-execution and the authenticated ledger record before accepting/committing state. Because the check never inspects `state_checkpoint_hash` (the main state Merkle root committed per checkpoint transaction), `hot_state_checkpoint_hash` (the hot-state root, gated by `HOT_STATE_ROOT_IN_TXN_INFO`), or `position_state_checkpoint_hash` (the native-position state root, gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`), a state root that differs from the authenticated one — due to a local execution bug, a storage bug in state-checkpoint hash computation (e.g. in `do_state_checkpoint.rs`), or a corrupted/malicious source during state-sync/backup replay — will not be caught by this specific integrity check. The `TransactionInfo` fields defined in `TransactionInfoV1` ( [3](#0-2) ) explicitly carry these three checkpoint hashes precisely because they are meant to be authenticated parts of ledger commitment, but the one function responsible for cross-checking re-executed output against them silently accepts a mismatch.

This directly matches the analog of the Size bug: an integrity/liquidity check exists and appears to protect the invariant, but is querying/comparing against an incomplete source (skips the state-commitment fields), so the "check" passes when it should fail, exactly as `validateVariablePoolHasEnoughLiquidity` checked the wrong balance and always "succeeded" incorrectly (there it failed when it should pass; here the analogous flaw is the inverse — it passes when it should fail, which is worse because it means corrupted state commitments go undetected).

### Impact Explanation
This breaks the fundamental proof/commit invariant that "committed state that differs from the correct VM result or corrupts durable ledger data" must be detected and rejected. If a chunk-executor replay or `replay_on_archive` re-execution produces a different state checkpoint root, hot-state root, or position-state root than what is authenticated in the proven `TransactionInfoV1` (e.g. due to a storage/restore bug, a JMT/hot-state computation regression, or a divergent state during fast-sync replay), `ensure_match_transaction_info` returns `Ok(())` regardless. Nodes performing chunk-based state sync or archive replay-verification can silently commit or "pass" with a wrong state root, defeating the very purpose of comparing against the accumulator-backed `TransactionInfo`. This is a high-severity gap in state-commitment integrity verification, matching the "wrong accumulator/state proof accepted as valid" category in the state-integrity gate.

### Likelihood Explanation
This is not a hypothetical: the feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, `HOT_STATE_ROOT_IN_TXN_INFO`) is under active development per the surrounding feature flags ( [4](#0-3) ) and gated for future mainnet enablement, and the code comment itself flags this exact scenario as a known, unaddressed pre-condition ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"). Any divergence in the underlying JMT/hot-state computation logic (which is new, complex code, e.g. `do_state_checkpoint.rs`, hot state promotion/eviction, position-state root logic) would not be caught by this gate today. Given the comment is explicit and unresolved in this snapshot of the repo, likelihood of the invariant currently being unenforced is confirmed by the code itself, though full triggering requires the trading-native/hot-state features to be enabled and a genuine local divergence to occur — which the code's own author considers a real, live risk.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` from `self`/computed output against the corresponding fields of `txn_info` whenever those fields are present (i.e., whenever the relevant feature is enabled), returning an error on mismatch just as is done for `write_set_hash` and `event_root_hash`. This should be resolved before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or `HOT_STATE_ROOT_IN_TXN_INFO` on any network, as the comment itself recommends.

### Proof of Concept
Not independently executable without a live devnet, since triggering requires: (1) enabling `HOT_STATE_ROOT_IN_TXN_INFO` and/or `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, (2) constructing a scenario where local re-execution's checkpoint hash diverges from the trusted `TransactionInfoV1` (e.g., replaying a chunk where the hot-state root differs due to a bug or corrupted hot-state DB), and (3) observing that `execution/executor/src/chunk_executor/mod.rs`'s call to `ensure_match_transaction_info` and `storage/db-tool/src/replay_on_archive.rs`'s call still return `Ok(())` despite the checkpoint-hash mismatch. This can be confirmed by unit-testing `ensure_match_transaction_info` directly: construct a `TransactionOutput` and a `TransactionInfoV1` with matching `state_change_hash`/`event_root_hash` but different `state_checkpoint_hash`/`hot_state_checkpoint_hash` and assert that the call still returns `Ok(())` — this is exactly what the source-code comment at lines 2196-2202 states occurs.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2203)
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
```

**File:** types/src/transaction/mod.rs (L2463-2494)
```rust
impl TransactionInfoV1 {
    pub fn new(
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
        Self {
            gas_used,
            status,
            transaction_hash,
            event_root_hash,
            state_change_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
            placeholder1: None,
            placeholder2: None,
            placeholder3: None,
            placeholder4: None,
            placeholder5: None,
            placeholder6: None,
            placeholder7: None,
        }
    }
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
