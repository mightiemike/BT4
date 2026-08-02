### Title
Replay/restore verification (`ensure_match_transaction_info`) silently accepts a divergent state/hot-state/position state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info`, the function used by chunk replay, backup restore, and replay-verify tooling to confirm that a locally re-executed transaction matches the authenticated `TransactionInfo` pulled from a proof/backup, checks status, gas used, write-set hash, and event root hash — but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. The state checkpoint hash is the Sparse-Merkle/JMT state root produced by the transaction, i.e. the actual proof-bound commitment of ledger state. A local execution that produces a different state root than the authenticated one will still pass this check.

### Finding Description
The check is implemented as: [1](#0-0) 

It verifies `status`, `gas_used`, the write-set hash against `state_change_hash`, and the event root hash, then returns `Ok(())` without ever comparing `self.state_checkpoint_hash()` (or hot-state/position variants) against `txn_info.state_checkpoint_hash()`/`hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()`. The code itself documents this gap: [2](#0-1) 

This function is the sole correctness gate used by replay/verification call sites: [3](#0-2) [4](#0-3) [5](#0-4) 

`chunk_executor` implements `TransactionReplayer`, which is exercised during backup restore and state-sync fast catch-up — paths that replay historical transactions against a trusted `TransactionInfo`/proof and must detect any divergence between local re-execution and the authenticated ledger state. Because the state-root fields are excluded from the comparison, a divergence in the state checkpoint (e.g., from a VM/state-tree bug, a non-deterministic native, or a corrupted intermediate state root introduced elsewhere in the executor-to-storage handoff) is not detected by this gate. The companion `position_state_checkpoint_hash` (native "position" JMT root, gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is likewise unchecked: [6](#0-5) 

### Impact Explanation
This breaks the "authenticated proof/storage handoff must preserve deterministic proof binding" invariant: a node performing replay-verify, debugger-based sanity checks, or backup/chunk restore can locally recompute a different state root than the one authenticated by the ledger's `TransactionInfo`/accumulator proof and still report the replay as successful. This can mask silent ledger-state corruption or a hidden execution-determinism bug that would otherwise manifest as consensus divergence, letting bad state persist undetected in restored databases or during forensic replay-verify audits — directly matching the "committed state differs from correct VM result" and "hard-fork-only divergence during replay/restore" impact categories.

### Likelihood Explanation
The root cause is a straightforward, low-complexity gap already flagged by the maintainers as a `TODO` before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, meaning it is a real, currently-shipped code path (not hypothetical) that any state-root-affecting bug or storage inconsistency will pass through undetected. No attacker privilege is required — the gap fires automatically whenever local state-root computation and the authenticated `TransactionInfo` disagree, regardless of cause.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on both sides) against the freshly computed roots before returning `Ok(())`, and fail loudly (equivalent to the existing `ensure!` failures for status/gas/write-set/events) on any mismatch.

### Proof of Concept
1. Run chunk replay/restore or `db-tool replay-on-archive` against a backup/chunk whose `TransactionInfo` carries a `state_checkpoint_hash` `H_expected`.
2. Have local VM execution (due to any state-root-affecting bug, e.g., a state-tree write/apply defect elsewhere in the pipeline) produce a differing root `H_actual` while write set hash, gas, status, and events remain identical.
3. `ensure_match_transaction_info` at [7](#0-6)  returns `Ok(())` because it never inspects `state_checkpoint_hash`.
4. The replay/restore tool reports success even though the locally committed state root diverges from the authenticated one.

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

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```

**File:** storage/db-tool/src/replay_on_archive.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```

**File:** execution/executor/src/chunk_executor/mod.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```

**File:** types/src/on_chain_config/aptos_features.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```
