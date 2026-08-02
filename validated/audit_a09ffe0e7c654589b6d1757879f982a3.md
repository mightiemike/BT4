### Title
`TransactionOutput::ensure_match_transaction_info` skips state-checkpoint hash verification, allowing replay/debugger tooling to accept a divergent state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` (used by `storage/db-tool/src/replay_on_archive.rs` and `aptos-move/aptos-debugger/src/aptos_debugger.rs` to validate that a locally re-executed transaction matches the authenticated `TransactionInfo` from a backup/archive) checks the transaction status, gas used, write-set hash (`state_change_hash`), and event root hash, but never checks `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`. This mirrors the WOOFi Solana bug class: a validity check that is supposed to bind a computed artifact to an authoritative record is incomplete/incorrect, so the guarded operation ("this replay is a faithful match") silently succeeds even when the underlying authenticated value (the state root) actually diverges.

### Finding Description [1](#0-0) 

`ensure_match_transaction_info` asserts equality for: `status`, `gas_used`, `write_set_hash == txn_info.state_change_hash()`, and `event_root_hash == txn_info.event_root_hash()`. It never reads or compares `txn_info.state_checkpoint_hash()` (nor `hot_state_checkpoint_hash`/`position_state_checkpoint_hash` on `TransactionInfoV1`) against anything computed from the replayed state. The function's own comment acknowledges this:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution.
```

`TransactionInfo` carries `state_checkpoint_hash` (and V1's `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) as separate, independently-hashed fields from `state_change_hash` [2](#0-1) . The write-set hash (`state_change_hash`) only proves the *delta* (write set) matches; it does not prove the resulting Merkle/JMT state root after applying that write set on top of the correct prior state matches the authenticated checkpoint hash. Two divergent underlying state trees (e.g., due to a prior undetected divergence, an execution-order or accumulation bug, or a mismatch between hot-state/position-state subsystems and the base state) can produce an identical write set yet different checkpoint roots, and this function would still return `Ok(())`.

`storage/db-tool/src/replay_on_archive.rs`'s `verify()`/`execute_and_verify()` path and `aptos-move/aptos-debugger/src/aptos_debugger.rs` rely on this function as the sole per-transaction correctness gate when replaying historical transactions from a backup against locally re-executed VM output.

### Impact Explanation
This breaks the "proof/storage pivot" invariant that replay and verification paths must not accept an authenticated result as valid when it actually diverges. Concretely: an operator or auditor running `db-tool replay-on-archive` (or `aptos-debugger` execute-replay) to confirm that an archived/backed-up chain history reproduces the authenticated ledger state will get a false "PASS" even if the actual Merkle/JMT state root (state_checkpoint_hash) computed from replay differs from the one recorded in the authenticated `TransactionInfo`. This is exactly the class of "hard-fork-only divergence during... replay... or proof verification" the review scope calls out: a state-root divergence between two conformant implementations (or between backup data and true chain state) would go undetected by the verification tool designed to catch it, undermining confidence in backup integrity and replay-based auditing/dispute resolution.

### Likelihood Explanation
This is not a hypothetical mis-transcription — it's directly acknowledged by an in-repo TODO showing the maintainers know the check is incomplete for the checkpoint-hash fields, but the function is already reachable today (not gated behind the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag) by the two real callers (`db-tool` and `aptos-debugger`), meaning the gap in verification exists in currently-shipped tooling paths, not merely in disabled experimental code. The trigger condition — any replayed write set that happens to hash identically while the resulting merged state diverges (or any bug in JMT commit / hot-state handling causing checkpoint hash mismatch) — is a normal storage-integrity event that this exact tool exists to catch, so the missing check meaningfully weakens the safety net the tool provides.

### Recommendation
Extend `ensure_match_transaction_info` to compute the actual state-checkpoint hash (and, for V1, hot-state / position-state checkpoint hashes when present) from the replayed state and assert equality against `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()` whenever these fields are `Some` in the expected `TransactionInfo`, mirroring the existing `ensure!` pattern already used for `state_change_hash` and `event_root_hash`.

### Proof of Concept
No dynamic PoC was run (no filesystem/terminal access); the finding is based on static code review of `types/src/transaction/mod.rs` lines 2139–2204 and its documented TODO plus its two callers (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`). A concrete PoC would require constructing a backup/archive `TransactionInfo` whose `state_checkpoint_hash` differs from what local re-execution of the corresponding write set produces (e.g., by injecting a divergent prior-state snapshot) and observing `ensure_match_transaction_info` return `Ok(())` despite the mismatch — this is unverified dynamically due to tooling limitations in this session.

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
