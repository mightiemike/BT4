## Finding

### Title
Replay/output verification (`ensure_match_transaction_info`) never checks state-checkpoint hashes, letting a diverged state root pass as valid - ([File: types/src/transaction/mod.rs])

### Summary
This is the closest Aptos analog to the CKB "uninitialized memory read regardless of cell kind" bug class: a validation routine that is supposed to gate all state-relevant fields of a committed result instead only checks a subset, silently letting a wrong/diverged value through as if it were verified. Here, `TransactionOutput::ensure_match_transaction_info` is the function used by replay/verification tooling to confirm that a locally produced `TransactionOutput` matches an authenticated `TransactionInfo` (the leaf object committed into the transaction accumulator and covered by validator signatures), but it never compares the state-checkpoint-related hash fields.

### Finding Description
`ensure_match_transaction_info` validates:
- `status` [1](#0-0) 
- `gas_used` [2](#0-1) 
- `write_set_hash` against `txn_info.state_change_hash()` [3](#0-2) 
- `event_root_hash` against `txn_info.event_root_hash()` [4](#0-3) 

It then returns `Ok(())` without ever comparing `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` against anything computed locally. The gap is explicitly called out in the code itself: [5](#0-4) 

This comment states directly that "this comparator ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution." That statement is not restricted to the unreleased trading-native feature — the ordinary `state_checkpoint_hash` field (the Sparse/Jellyfish Merkle root of world state at that version) is likewise excluded from the comparison, for every call site.

`ensure_match_transaction_info` is consumed by tooling whose entire purpose is state-commitment integrity verification: `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`, and `execution/executor/src/chunk_executor/mod.rs`. These call sites feed `TransactionInfo` values sourced from an authenticated backup/archive or from state-sync-provided ledger data (bound to a signed `LedgerInfo` via `TransactionInfoListWithProof`/accumulator proofs, see `types/src/proof/definition.rs`), and rely on this function as the pass/fail gate that the re-executed output is faithful to what was actually committed on-chain.

Because the function never checks the state-checkpoint (world-state) root, a divergence in the Jellyfish Merkle root produced by local re-execution — caused by a non-determinism bug, storage schema mismatch, or a state-commit defect elsewhere in the pipeline — is not detected by this gate. The function reports success purely because write-set and event hashes matched, even though the accumulated/rolled-up world-state root recorded in the authenticated `TransactionInfo` differs from what local execution actually produced.

### Impact Explanation
This breaks the "committed state that differs from the correct VM result... must be caught by replay/commit verification" invariant. Replay-verify is the safety net specifically designed to catch state-root divergence (the exact class of bug that produces the hard-fork scenario described in the report) before it silently propagates. If this comparator is relied upon as the pass/fail signal, a real state-commitment bug can pass replay verification undetected, delaying discovery of a consensus-affecting divergence until it manifests as an actual fork on mainnet. This is a high-severity gap because it defeats the detection mechanism meant to prevent exactly the class of incident this report is analogizing to (a state divergence between correct and buggy execution that surfaces only as a hard fork).

### Likelihood Explanation
The code path is unprivileged and always reachable by anyone running the affected tools (`aptos-debugger`, CLI replay commands, or a full node's chunk executor during backward-compatible verification/replay flows) — no special permissions are needed to trigger a comparison; the only precondition is that a state-root-affecting execution bug or non-determinism exists somewhere else in the stack, which is exactly the scenario replay-verify exists to catch. The gap is also explicitly documented by the code's own author as a known blind spot, confirming it is a real, present limitation rather than a hypothetical one.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and (when applicable) `position_state_checkpoint_hash` computed by the replay/execution path against the corresponding fields of the authenticated `txn_info`, whenever those fields are expected to be present (i.e., at checkpoint boundaries). At minimum, gate any code that currently treats this function's `Ok(())` as full proof of state-commitment correctness, and fail loudly (not silently pass) when checkpoint hashes cannot be verified.

### Proof of Concept
Not directly exploitable as a standalone PoC against consensus (this is a verification-tooling gap, not a consensus-code-path bug), but demonstrable by construction:
1. Produce a `TransactionOutput` whose write set and events match an authenticated `TransactionInfo`, but whose world-state root (as would be recomputed via `do_state_checkpoint`) differs from `txn_info.state_checkpoint_hash()`.
2. Call `ensure_match_transaction_info(version, &txn_info, ..)` — per `types/src/transaction/mod.rs:2139-2203`, this returns `Ok(())` despite the state root mismatch, because no comparison against `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` is performed.

Note: I was unable to fully trace whether the normal validator commit/chunk-execution pipeline (`do_state_checkpoint.rs`, which does separately validate `known_state_checkpoints` at [6](#0-5) ) is always invoked alongside `ensure_match_transaction_info` in every one of its three call sites, or whether some tooling paths (e.g., `aptos-debugger`/CLI replay) rely on `ensure_match_transaction_info` alone as their only integrity check. This distinction determines whether the gap is a redundant/defense-in-depth omission or a genuinely unguarded path in some tool invocations; further investigation of `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs` call sites would be needed to confirm the precise blast radius.

### Citations

**File:** types/src/transaction/mod.rs (L2148-2157)
```rust
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
```

**File:** types/src/transaction/mod.rs (L2159-2166)
```rust
        ensure!(
            self.gas_used() == txn_info.gas_used(),
            "{}: version:{}, gas_used:{:?}, expected:{:?}",
            ERR_MSG,
            version,
            self.gas_used(),
            txn_info.gas_used(),
        );
```

**File:** types/src/transaction/mod.rs (L2168-2178)
```rust
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
```

**File:** types/src/transaction/mod.rs (L2180-2195)
```rust
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
```

**File:** types/src/transaction/mod.rs (L2197-2203)
```rust
        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
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
