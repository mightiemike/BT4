## Finding

### Title
`TransactionOutput::ensure_match_transaction_info` omits state/hot-state/position checkpoint hash checks, allowing replay-verify to accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`ensure_match_transaction_info`, the function used by replay/debugger tooling (`aptos-move/aptos-debugger/src/aptos_debugger.rs`, `storage/db-tool/src/replay_on_archive.rs`, `aptos-move/cli/src/commands.rs`) to confirm that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` recorded on-chain, only compares status, gas used, write-set hash, and event root hash. It does not compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`.

### Finding Description
The commit path (`execution/executor/src/workflow/do_ledger_update.rs`, `assemble_transaction_infos`) builds a `TransactionInfo` containing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, all of which are folded into the accumulator leaf hash and ultimately the ledger's `transaction_accumulator_hash` bound in each `LedgerInfo`. [1](#0-0) 

However, `ensure_match_transaction_info`, which replay/debugger tooling calls to verify that locally executed output reproduces the authenticated on-chain result, checks only `status`, `gas_used`, the write-set hash, and the event root hash — it never checks `state_checkpoint_hash` (nor the hot-state or position-state checkpoint hashes carried in `TransactionInfoV1`) against the corresponding value derivable from local execution: [2](#0-1) 

The code itself documents this gap with a TODO: [3](#0-2) 

This function is the sole state-integrity check used by `replay_on_archive` and the `aptos-debugger`/CLI replay-verify paths: [4](#0-3) [5](#0-4) 

Because the world-state root (the Sparse/Jellyfish Merkle checkpoint hash) is exactly the value that would diverge from a hard-fork-causing bug (e.g., an executor bug that mutates state incorrectly while leaving the write set's *shape* — keys/values as serialized in the write set itself — hash-compatible, or a bug that corrupts the merklization/commit step downstream of the write set), a divergence limited to the checkpoint hash silently passes this check. The write-set hash comparison catches divergence in the *output of VM execution*, but not divergence introduced further down the pipeline in the state-checkpoint/Merkle-commit stage (`do_state_checkpoint.rs`, JMT/hot-state application) — which is precisely the "storage schemas, replay paths, and restore helpers must not reinterpret committed data into a different ledger state" class of failure this task calls out.

### Impact Explanation
This does not corrupt the actual consensus-committed ledger (the accumulator/`LedgerInfo` construction in `do_ledger_update.rs` is unaffected and still binds the real checkpoint hashes). The impact is confined to the *detection* mechanism: replay-verify and debugger tooling used to authenticate historical execution against archived, signed `TransactionInfo`s can report "replay successful" even when the locally recomputed state-checkpoint root (i.e., the actual world state resulting from replay) diverges from the authenticated one. This directly undermines a stated in-scope invariant ("Authenticated API and proof-bearing responses must stay bound to the right ledger version, root, and object" / "Hard-fork-only divergence during commit, replay, restore, or proof verification") because it is exactly the tool relied on to detect state-root divergence across upgrades/hard forks, and it would fail to flag a real divergence in the most safety-critical field (the state root) while reporting success.

### Likelihood Explanation
Low-to-moderate. This requires an underlying (separate) bug in state-checkpoint/JMT commit logic that produces a different state root while still producing byte-identical write sets/events/gas/status — a fairly narrow bug class, and this gap is already flagged with a TODO in the source, indicating the maintainers are aware and it is only exercised in offline verification tooling, not in the live consensus/commit critical path.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash` (and, when present, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) between the locally recomputed checkpoint and `txn_info`'s fields before enabling any feature (e.g., `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that depends on trusting replay-verify's pass/fail signal for these roots.

### Proof of Concept
No runnable PoC is provided: this is a static-analysis finding based on the mismatch between fields hashed into `TransactionInfo`/accumulator (`do_ledger_update.rs:95-121`) and fields actually compared in `ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`), corroborated by the code's own TODO comment acknowledging the gap. I was unable to fully trace how `replay_on_archive.rs` and `aptos_debugger.rs` consume the `Result` of this check (e.g., whether they log-only vs. hard-fail) due to running out of tool iterations before reading those files in full — this should be verified by a follow-up session with filesystem access.

### Citations

**File:** execution/executor/src/workflow/do_ledger_update.rs (L82-121)
```rust
                let state_checkpoint_hash = state_checkpoint_hashes[i];
                let event_hashes = txn_output
                    .events()
                    .iter()
                    .map(CryptoHash::hash)
                    .collect::<Vec<_>>();
                let event_root_hash =
                    InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash();
                let write_set_hash = CryptoHash::hash(txn_output.write_set());
                let status = txn_output
                    .status()
                    .as_kept_status()
                    .expect("Already sorted.");
                let txn_info = if transaction_info_v1 {
                    TransactionInfo::builder_v1()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .maybe_hot_state_checkpoint_hash(
                            hot_state_checkpoint_hashes.and_then(|hot| hot[i]),
                        )
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .maybe_position_state_checkpoint_hash(
                            position_state_checkpoint_hashes.and_then(|p| p[i]),
                        )
                        .build()
                } else {
                    TransactionInfo::builder_v0()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .build()
                };
```

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

**File:** storage/db-tool/src/replay_on_archive.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```
