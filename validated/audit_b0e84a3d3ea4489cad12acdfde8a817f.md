## Finding: `TransactionOutput::ensure_match_transaction_info` silently skips state-checkpoint-hash validation, defeating replay-verify's proof-binding guarantee

### Title
Replay-verify integrity check omits state/hot-state/position checkpoint hash comparison, allowing silent state-root divergence to pass as "verified" - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative equality check used by replay/verification tooling to confirm that a locally re-executed `TransactionOutput` matches the trusted, network-synced `TransactionInfo` (which is itself bound to a Merkle-accumulator-verified `LedgerInfo`/proof). The function checks status, gas used, write-set hash, and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents via a `TODO`.

### Finding Description [1](#0-0) 

The function compares:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `CryptoHash::hash(self.write_set())` vs `txn_info.state_change_hash()`
- computed `event_root_hash` vs `txn_info.event_root_hash()`

It never reads or compares `txn_info.state_checkpoint_hash()` (the authenticated Sparse-Merkle-Tree root of world state at a checkpoint) against any locally computed state root, nor the hot-state or position-state checkpoint hashes. The comment makes this explicit: [2](#0-1) 

This function is used as the core "did my replay match the authenticated chain" invariant in `storage/db-tool/src/replay_on_archive.rs`, `execution/executor/src/chunk_executor/mod.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs`. [3](#0-2) 

Note the write-set hash check only covers `state_change_hash`, which is derived from the write set itself, not from the merklized world-state root produced by applying the write set on top of prior state. A discrepancy in the JMT/SMT-merge logic, key-hashing, or state-checkpoint construction (executor-to-storage handoff) would change the state root but leave `state_change_hash` and `event_root_hash` untouched, so this check would not detect it.

### Impact Explanation
Replay-verify tooling (`db-tool replay-on-archive`, chunk executor replay paths, and the Aptos debugger) is the primary mechanism for catching state divergence between VM/storage changes and the real, proof-authenticated chain history before/after upgrades. Because `ensure_match_transaction_info` never compares the state checkpoint hash, a bug in state-merge/Merkle-tree construction, restore flows, or the "trading-native"/position state root computation (explicitly called out in the TODO) can produce a wrong state root while `ensure_match_transaction_info` still reports success. This is exactly the class of "hard-fork-only divergence during commit, replay, or restore" and "wrong ... state proof accepted as valid" that the scope calls out: a corrupted committed state can pass verification tooling undetected, masking a critical, potentially chain-splitting bug rather than causing it directly. The severity is high because it removes the primary safety net meant to catch exactly this class of bug prior to it reaching mainnet.

### Likelihood Explanation
This is not an externally triggerable exploit by an unprivileged attacker in the traditional sense — it is a gap in an internal correctness-verification tool. However, it is a genuine, confirmed-by-code-comment root cause (not speculative): the comment in `ensure_match_transaction_info` explicitly acknowledges that checkpoint hashes are ignored and that this must be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" — i.e., the maintainers are aware a feature is being staged whose correctness cannot currently be verified by this tooling. Any latent bug in state-checkpoint construction (JMT merge, hot-state, or the new position-state feature) would silently escape detection whenever this code path is relied upon, which is routinely on real archival data.

### Recommendation
Extend `ensure_match_transaction_info` to also compare locally computed state checkpoint hash(es) (state, hot-state, and position-state, when present) against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` whenever those fields are `Some`, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or any dependent feature is enabled.

### Proof of Concept
Code-level proof (no external harness needed, since the root cause is a documented omission in local code):
1. `ensure_match_transaction_info` at [1](#0-0)  only asserts equality on `status`, `gas_used`, `state_change_hash` (write-set hash), and `event_root_hash`.
2. It receives `txn_info: &TransactionInfo`, which carries `state_checkpoint_hash()`, `hot_state_checkpoint_hash()` (V1), and `position_state_checkpoint_hash()` (V1) as defined in `TransactionInfoV0`/`TransactionInfoV1` at [4](#0-3) , none of which are read inside the function body.
3. Any caller relying on this function (e.g., `storage/db-tool/src/replay_on_archive.rs`) to assert "local replay equals authenticated chain data" will return `Ok(())` even if the locally computed state checkpoint root diverges from the one bound into the accumulator-proven `TransactionInfo`, because the comparison is never performed.

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

**File:** types/src/transaction/mod.rs (L2388-2461)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct TransactionInfoV0 {
    /// The amount of gas used.
    gas_used: u64,

    /// The vm status. If it is not `Executed`, this will provide the general error class. Execution
    /// failures and Move abort's receive more detailed information. But other errors are generally
    /// categorized with no status code or other information
    status: ExecutionStatus,

    /// The hash of this transaction.
    transaction_hash: HashValue,

    /// The root hash of Merkle Accumulator storing all events emitted during this transaction.
    event_root_hash: HashValue,

    /// The hash value summarizing all changes caused to the world state by this transaction.
    /// i.e. hash of the output write set.
    state_change_hash: HashValue,

    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,

    /// The hash value summarizing PersistedAuxiliaryInfo.
    auxiliary_info_hash: Option<HashValue>,
}

impl TransactionInfoV0 {
    pub fn new(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
    ) -> Self {
        Self {
            gas_used,
            status,
            transaction_hash,
            event_root_hash,
            state_change_hash,
            state_checkpoint_hash,
            auxiliary_info_hash,
        }
    }
}

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

**File:** storage/db-tool/src/replay_on_archive.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```
