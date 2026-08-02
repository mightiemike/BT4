### Title
`TransactionOutput::ensure_match_transaction_info` skips checkpoint-hash verification, allowing corrupted state roots to pass replay/proof checks - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative comparator used to confirm that a locally re-executed transaction output matches the authenticated `TransactionInfo` carried in ledger/replay data. It checks status, gas, write-set hash, and event-root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that actually commit to the state-tree root. This mirrors the reported bug class: a commitment/verification routine that hashes/compares only a subset of the state-relevant fields, letting a diverging critical value ("_init"/"_calldata" analog here being the state-checkpoint roots) slip through unauthenticated.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates status and gas, then at [2](#0-1)  validates `write_set_hash` against `txn_info.state_change_hash()` and `event_root_hash` against `txn_info.event_root_hash()`. The function ends with an explicit, self-documented gap: [3](#0-2) 

This comment states the comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), meaning replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position/state root diverges from local execution.

The `TransactionInfoV0`/`V1` structs carry these checkpoint roots as first-class commitment fields alongside `state_change_hash` and `event_root_hash`: [4](#0-3)  and [5](#0-4) . These are exactly the fields whose correctness is central to committed-state integrity — `state_checkpoint_hash` is the Sparse-Merkle root of world state at a checkpoint, and `position_state_checkpoint_hash` is a repurposed field tied to the newer "trading-native" state root design gated by the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag (referenced in `types/src/on_chain_config/aptos_features.rs` and `storage/aptosdb/src/db/aptosdb_reader.rs`). Because the comparator used by replay/verification call sites (e.g. `aptos-move/cli/src/commands.rs`, which calls `ensure_match_transaction_info` for the CLI's `Replay` command) never checks these roots, a divergence between the locally-computed state root and the authenticated on-chain `TransactionInfo` state root would not be caught by this check.

### Impact Explanation
If the executor or storage layer ever produces an incorrect state-checkpoint root (due to a bug in JMT/SMT construction, a hard fork mismatch, or corrupted storage), tooling relying on `ensure_match_transaction_info` — including replay/audit tooling — would report a clean, verified match even though the actual world-state root diverged from the authenticated ledger commitment. This is a proof-integrity blind spot: it allows "wrong accumulator/state root accepted as valid" during replay verification, undermining confidence that replayed state matches the canonical chain. It does not appear to bypass the primary Merkle-accumulator/ledger-info signature verification path in `types/src/proof/definition.rs` (i.e., consensus itself still binds to `transaction_accumulator_hash()`), but it is a real gap in the auxiliary self-consistency check tools rely on for detecting state divergence.

### Likelihood Explanation
This is a self-documented gap (see the TODO in the code itself) that is always present whenever `ensure_match_transaction_info` is invoked without a supplementary checkpoint-hash check — no privileged access or malicious governance is required, it is a structural omission in the verification routine. The likelihood of it silently masking a genuine divergence depends on the underlying execution/storage code actually producing a wrong checkpoint root, which is a separate, non-trivial precondition; I could not fully trace whether any other call site independently re-validates the checkpoint hashes before/after this function is used (time/tooling constraints prevented full tracing into `aptos-move/cli/src/local_simulation.rs` or `dispatch_transaction` to confirm whether callers add extra checks).

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally recomputed values whenever they are `Some` in the `TransactionInfo`, consistent with how `write_set_hash` and `event_root_hash` are already checked, before enabling/relying on `COMPUTE_TRADING_NATIVE_STATE_ROOTS`-based tooling for authoritative replay verification.

### Proof of Concept
Not applicable as an exploit PoC — this is a code-verifiable comparator gap, not an attacker-triggerable state corruption by itself. The proof is the function body itself: [6](#0-5)  shows the four checks performed (status, gas, write-set hash, event-root hash) and the explicit omission of the checkpoint-hash fields, confirmed by the inline TODO comment acknowledging the exact risk (replay-verify tooling reporting success despite root divergence).

**Caveat:** Because this is a self-acknowledged TODO in the codebase rather than a silently-introduced bug, and I was unable to fully confirm (due to iteration limits) whether any downstream caller independently re-validates the checkpoint hashes, treat this as a confirmed code-level gap whose full exploitability chain (i.e., whether any live path actually depends solely on this function for state-root integrity) is not fully verified.

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

**File:** types/src/transaction/mod.rs (L2402-2416)
```rust
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
