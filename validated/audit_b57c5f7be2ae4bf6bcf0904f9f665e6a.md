### Title
Replay-verification bypasses authenticated position-state / hot-state checkpoint roots in `TransactionOutput::ensure_match_transaction_info` - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity gate used to confirm that a locally-recomputed `TransactionOutput` (write set + events + status + gas) matches the authenticated `TransactionInfo` stored in the accumulator/ledger for a given version. It is invoked by replay-verification tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`) to detect divergence between local execution and the committed/authenticated chain state. The function checks status, gas, write-set hash (`state_change_hash`), and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` carried by `TransactionInfoV1`.

### Finding Description [1](#0-0) 

The function computes and compares only three fields:
- transaction status vs `txn_info.status()`
- `gas_used()` vs `txn_info.gas_used()`
- `CryptoHash::hash(self.write_set())` vs `txn_info.state_change_hash()`
- event root hash (built from `InMemoryEventAccumulator`) vs `txn_info.event_root_hash()`

It returns `Ok(())` without ever inspecting `TransactionInfoV1::state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, even though these fields exist specifically to authenticate the post-transaction state/hot-state/position-state Merkle roots [2](#0-1) .

The code itself documents this gap with an explicit TODO immediately preceding the `Ok(())`: [3](#0-2) 
> "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

These checkpoint hashes are produced by `DoStateCheckpoint`/`DoLedgerUpdate` in the normal execution pipeline and threaded into `TransactionInfo` via `assemble_transaction_infos` [4](#0-3) , and `get_state_checkpoint_hashes` explicitly asserts consistency of the computed checkpoint hash against known/expected values on the live commit path [5](#0-4) . So on the primary execution/commit path this invariant is enforced. The gap is isolated to the standalone verification function used by offline replay/debugging tools that re-derive `TransactionOutput` independently (e.g. against an archive) and rely on `ensure_match_transaction_info` as the sole correctness oracle.

### Impact Explanation
Because `ensure_match_transaction_info` silently accepts any `TransactionOutput` whose state/hot-state/position-state checkpoint roots differ from the authenticated `TransactionInfo`, tooling built on top of it (`replay_on_archive`, `aptos-debugger`) can report a clean/successful replay even though the locally computed post-state Merkle root diverges from the on-chain authenticated root. This is a real proof-binding gap: an authenticated response (the accumulator-committed `TransactionInfo`) is not actually validated against the locally computed checkpoint state, defeating the purpose of replay-verification as an audit/consistency mechanism for detecting non-determinism, hard-fork divergence, or storage corruption around the position-state/hot-state root feature path.

That said, the severity is bounded: it does not corrupt committed ledger state or forge a validly-verifying Merkle/accumulator proof against a light client — it only weakens an internal/offline diagnostic tool's ability to detect an already-existing divergence. It requires the new `compute_trading_native_state_roots` / `TRANSACTION_INFO_V1` position-state feature path to be active for the divergent field to exist at all in `TransactionInfoV1`. I could not fully confirm from the index whether this feature is enabled on mainnet or still gated as experimental (`compute_trading_native_state_roots` appears wired through `BlockExecutorConfigFromOnchain` and `AptosFeature`s, but I was not able to verify its current mainnet activation status within the available iterations).

### Likelihood Explanation
The bug requires no privileged access to trigger — it's a missing comparison, not a rare race — but it only manifests when (a) `TransactionInfoV1`/hot-state or position-state checkpoint hashing is active, and (b) someone relies on `ensure_match_transaction_info` to detect divergence (replay/debug tooling), which is a lower-frequency, non-consensus-critical code path. The comment in the code confirms the maintainers are already aware of this exact gap, indicating it's a known but unresolved TODO rather than a subtle undiscovered flaw.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on `TransactionInfoV1`) against the corresponding locally recomputed checkpoint roots before returning `Ok(())`, so replay-verify and debugger tooling cannot report success while the authenticated position/hot state root has diverged.

### Proof of Concept
Not applicable as an executable PoC — the flaw is a code-level omission, self-documented in the source. Trace: `replay_on_archive.rs` / `aptos_debugger.rs` call `TransactionOutput::ensure_match_transaction_info` → function checks status/gas/write_set_hash/event_root_hash only → returns `Ok(())` regardless of any mismatch in `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` fields of `TransactionInfoV1` [1](#0-0)  — meaning divergent post-state roots pass verification silently.

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

**File:** types/src/transaction/mod.rs (L2463-2493)
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
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L30-45)
```rust
        // Assemble `TransactionInfo`s. The variant (V0 vs V1) is driven by the
        // `TRANSACTION_INFO_V1` on-chain feature, threaded via
        // `ExecutionOutput::transaction_info_v1`. The hot state root hash a V1 carries is
        // present only when `HOT_STATE_ROOT_IN_TXN_INFO` is also on (`DoStateCheckpoint`
        // produces `Some` hashes iff so); otherwise the V1 leaves it `None`.
        let (transaction_infos, transaction_info_hashes) = Self::assemble_transaction_infos(
            &execution_output.to_commit,
            execution_output.transaction_info_v1,
            &state_checkpoint_output.state_checkpoint_hashes,
            state_checkpoint_output
                .hot_state_checkpoint_hashes
                .as_deref(),
            state_checkpoint_output
                .position_state_checkpoint_hashes
                .as_deref(),
        );
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L206-220)
```rust
        if let Some(known) = known_state_checkpoints {
            ensure!(
                known.len() == num_txns,
                "Bad number of known {label} hashes. {} vs {}",
                known.len(),
                num_txns,
            );
            if let Some(idx) = last_checkpoint_index {
                ensure!(
                    known[idx] == Some(computed_last_checkpoint_hash),
                    "{label} root hash mismatch with known hashes passed in. {:?} vs {:?}",
                    known[idx],
                    Some(computed_last_checkpoint_hash),
                );
            }
```
