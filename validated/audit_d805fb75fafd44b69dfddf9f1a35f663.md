### Title
`ensure_match_transaction_info` skips checkpoint-hash validation, allowing state-checkpoint/hot-state/position-state root divergence to pass replay and apply-transaction-output verification - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity check used by chunk-execution/replay paths to confirm a locally-produced `TransactionOutput` matches the authenticated `TransactionInfo` fetched from a peer, an archive, or a backup before it is accepted as correct. The function validates status, gas used, write-set hash (`state_change_hash`), and event root hash, but it explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This mirrors the external report's pattern: an integrity/authorization check that special-cases (silently skips) a field it should validate, letting a downstream flow accept state that doesn't actually correspond to what was verified.

### Finding Description
The comparator function is defined in `types/src/transaction/mod.rs`: [1](#0-0) 

It checks `status`, `gas_used`, the write-set hash against `txn_info.state_change_hash()`, and the event root hash against `txn_info.event_root_hash()`. The code itself contains an explicit acknowledgment of the gap:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```

`state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` are all part of `TransactionInfo`/`TransactionInfoV1` and feed the transaction-accumulator leaf hash (`TransactionInfo::hash()`), i.e. they are consensus-committed, proof-bearing fields: [2](#0-1) 

`ensure_match_transaction_info` is called from `execution/executor/src/chunk_executor/mod.rs` (chunk apply/replay verification) as well as from the CLI/debugger replay tooling (`aptos-move/cli/src/commands.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`). Because the checkpoint-hash fields are excluded from the comparison, a chunk executor or replay tool can compute a *different* state/hot-state/position-state root than what is embedded in the authenticated `TransactionInfo` (and therefore committed into the transaction accumulator under the peer's `LedgerInfo`), yet the comparison still reports success. The `TRANSACTION_INFO_V1`, `HOT_STATE_ROOT_IN_TXN_INFO`, and `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flags gate when these fields are populated: [3](#0-2) 

### Impact Explanation
This breaks the "committed state that differs from the correct VM result... must not be accepted" and "hard-fork-only divergence during commit, replay, restore" invariants named in the scan brief. Once `TRANSACTION_INFO_V1`/`HOT_STATE_ROOT_IN_TXN_INFO`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS` are enabled on mainnet, a node applying transaction outputs (fast-sync, chunk executor "apply transaction outputs" mode, or replay/backup-restore tooling) will accept and persist a `TransactionInfo`/ledger state whose state-checkpoint, hot-state, or position-state root does not match what its own execution produced, without detecting the divergence through this particular guard. This is exactly a "wrong accumulator root ... accepted as valid" and "authenticated API/state-view output bound to the wrong version/object" class of issue, since the position/hot state root persisted no longer provably reflects the actual state tree it claims to summarize.

### Likelihood Explanation
Likelihood is currently constrained: the affected fields are non-`None` only when `TRANSACTION_INFO_V1`, `HOT_STATE_ROOT_IN_TXN_INFO`, and (for the position-state field) `COMPUTE_TRADING_NATIVE_STATE_ROOTS` are enabled via governance — features that were not confirmed to be active on mainnet at the time of this scan. The gap is also explicitly flagged as a known TODO in the code (guarding the rollout of `COMPUTE_TRADING_NATIVE_STATE_ROOTS`), meaning it is a recognized, pre-existing rollout gate rather than a novel silent bug. It does not require any privileged access to trigger — any node performing replay/apply-outputs sync with a divergent local execution result (e.g. due to a client bug, non-determinism, or an intentionally malicious data provider serving mismatched-but-hash-consistent write sets) would go undetected by this specific check, but other checks (accumulator range-proof verification against the target `LedgerInfo`, discussed in `types/src/proof/definition.rs`) still constrain what data can be accepted overall, since the leaf hash of `TransactionInfo` (which includes the checkpoint hashes) is proof-verified elsewhere.

### Recommendation
Extend `ensure_match_transaction_info` to also assert equality of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when locally computed) against the corresponding fields on `txn_info`, consistent with the TODO already present in the code, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (and ideally before `HOT_STATE_ROOT_IN_TXN_INFO`) is enabled by governance.

### Proof of Concept
Not independently exploitable without the noted feature flags active; the finding is proven directly by local code inspection: `ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) omits comparison of `state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()` fields defined on `TransactionInfo`/`TransactionInfoV1` (`types/src/transaction/mod.rs:2352-2364`, `2440-2461`), and its own in-code TODO comment confirms this causes `db-tool`'s `replay_on_archive` (and by extension chunk-executor apply-output verification) to report success even when the authenticated position/hot-state root diverges from local computation. [4](#0-3)

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
