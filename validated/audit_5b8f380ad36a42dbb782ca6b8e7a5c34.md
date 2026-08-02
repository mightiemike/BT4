## Finding: `ensure_match_transaction_info` in `types/src/transaction/mod.rs` does not verify state-checkpoint hashes, letting replay-verify accept a divergent state root

### Title
Replay-verify integrity check skips state/hot-state/position checkpoint hash comparison, masking state-root divergence - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function that compares a locally re-executed `TransactionOutput` against the authenticated `TransactionInfo` pulled from storage/proofs during replay and chunk execution. It validates status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that summarize the actual state root at that version. [1](#0-0) 

### Finding Description
The function computes and checks `write_set_hash` against `txn_info.state_change_hash()` and the event root against `txn_info.event_root_hash()`, but the code contains an explicit acknowledgment that the state-root fields are skipped: [2](#0-1) 

The comment states verbatim that this comparator "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution," and that checkpoint hash validation must be added before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled. [3](#0-2) 

`ensure_match_transaction_info` is called from `storage/db-tool/src/replay_on_archive.rs` and `execution/executor/src/chunk_executor/mod.rs` (and `aptos-move/cli/src/commands.rs`), which are exactly the state-integrity pivot points named in the task: replay paths and chunk/state-sync execution that must not "reinterpret committed data into a different ledger state." [4](#0-3) [5](#0-4) 

Because `TransactionInfo` is normally authenticated via an accumulator proof against a trusted `LedgerInfo` before this comparator runs, the checkpoint hash embedded in the *remote/persisted* `TransactionInfo` is itself trustworthy. The actual danger is the reverse direction: this function's job is to confirm that *local re-execution* produced the same result as the authoritative chain. Since the state-checkpoint hash (the field that actually encodes the state root — the primary artifact these tools exist to validate) is skipped, a local execution bug, non-determinism, or software regression that produces a **different state root** than the authoritative chain will not be caught by `ensure_match_transaction_info`. `replay_on_archive` and the chunk executor's `ensure_transaction_infos_match`/verification pipeline rely on this comparator as a determinism/integrity gate.

### Impact Explanation
This breaks the core proof/replay invariant: "committed state that differs from the correct VM result... must not be accepted." A state-root divergence between the archived/authoritative chain and locally re-executed output — the exact class of bug that would herald a consensus/hard-fork-causing non-determinism issue — can silently pass `replay_on_archive` and equivalent chunk-executor verification, because the state checkpoint (SMT/JMT root) fields are never compared. This falls squarely into the explicitly in-scope category: "Hard-fork-only divergence during commit, replay, restore, or proof verification." It undermines confidence in replay-verify as a safety net for catching state-divergent execution bugs before/after a network upgrade.

### Likelihood Explanation
This is not an attacker-triggered exploit but a code-confirmed logic gap acknowledged directly by an in-repo TODO comment. It will manifest whenever a genuine execution/state divergence occurs (e.g., a VM bug, feature-flag interaction, or upgrade regression) that changes state without changing the write set or events for the transaction in question — likelihood of the underlying divergence event is uncertain, but the *detection failure* is deterministic and always active. The comment further indicates the project itself flags this as a blocker for enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, confirming this is a known-real gap rather than a false positive.

### Recommendation
Add explicit comparisons of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (where present in the local re-computed state) against the same fields on `txn_info` inside `ensure_match_transaction_info`, before this comparator is relied upon by replay-verify tooling or before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, exactly as the existing TODO comment specifies.

### Proof of Concept
Not independently exploitable by an external attacker; the code path is a self-acknowledged gap. Verification steps:
1. Inspect `ensure_match_transaction_info` at `types/src/transaction/mod.rs:2139-2204` — note only `status`, `gas_used`, `write_set` (`state_change_hash`), and `event_root_hash` are checked.
2. Confirm `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` fields exist on `TransactionInfo`/`TransactionInfoV1` (`types/src/transaction/mod.rs:2440-2461`) but are absent from the comparator.
3. Confirm call sites in `storage/db-tool/src/replay_on_archive.rs` and `execution/executor/src/chunk_executor/mod.rs` use this comparator as their correctness gate for replayed/synced transactions. [6](#0-5)

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

**File:** storage/db-tool/src/replay_on_archive.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```

**File:** execution/executor/src/chunk_executor/mod.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```
