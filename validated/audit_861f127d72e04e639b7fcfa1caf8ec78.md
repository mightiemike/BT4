### Title
`TransactionOutput::ensure_match_transaction_info` never validates the state (or hot-state) checkpoint hash, allowing replay/chunk-apply verification to accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity check used by state-sync chunk application, replay tooling, the Aptos debugger and the CLI to confirm that locally re-executed transaction output matches the authenticated `TransactionInfo` from a signed `LedgerInfo`/transaction proof before that output (or the associated state) is treated as valid and persisted. The function checks `status`, `gas_used`, `write_set` hash (`state_change_hash`) and `event_root_hash`, but it never compares the resulting `state_checkpoint_hash` (or `hot_state_checkpoint_hash`) against the value carried in the authenticated `TransactionInfo`.

### Finding Description [1](#0-0) 

```rust
pub fn ensure_match_transaction_info(
    &self,
    version: Version,
    txn_info: &TransactionInfo,
    expected_write_set: Option<&WriteSet>,
    expected_events: Option<&[ContractEvent]>,
) -> Result<()> {
    ...
    ensure!(self.status() == &expected_txn_status, ...);
    ensure!(self.gas_used() == txn_info.gas_used(), ...);
    let write_set_hash = CryptoHash::hash(self.write_set());
    ensure!(write_set_hash == txn_info.state_change_hash(), ...);
    let event_root_hash = InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash;
    ensure!(event_root_hash == txn_info.event_root_hash(), ...);

    // TODO(trading-native): this comparator ignores the checkpoint hashes
    // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
    // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
    // replay even when the authenticated position state root diverges from
    // local execution. Validate the checkpoint hashes here before enabling
    // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
    Ok(())
}
```

The `state_change_hash` field only commits to the *write set bytes themselves* — it says nothing about whether applying that write set to the previous JMT/state tree produces the correct new state root. `state_checkpoint_hash` is the field that binds the transaction to the actual, cumulative Merkle state root (`TransactionInfo::state_checkpoint_hash`, see [2](#0-1) ), and it is the value that downstream consumers rely on to prove/verify the ledger's committed state. Because `ensure_match_transaction_info` never asserts `state_checkpoint_hash == <locally computed state-tree root>`, any divergence between the JMT/state-tree computation and the previously authenticated state root (e.g., a state-key hashing bug, an incorrect stale-index/shard update, a non-deterministic accumulator update, or corrupted persisted state feeding into checkpoint computation) will not be caught by this check, even though the write set and events match byte-for-byte.

This function is the sole cross-check used by:
- `storage/db-tool/src/replay_on_archive.rs` (`Verifier::execute_and_verify`, line 392) — used to independently verify archived history against re-execution,
- `execution/executor/src/chunk_executor/mod.rs`,
- `aptos-move/aptos-debugger/src/aptos_debugger.rs`,
- `aptos-move/cli/src/commands.rs`. [3](#0-2) 

The comment itself is written by the codebase's own authors confirming the gap: replay-verify tooling "can report a successful replay even when the authenticated position state root diverges from local execution."

### Impact Explanation
Because this check omits `state_checkpoint_hash`/`hot_state_checkpoint_hash` unconditionally (not merely behind the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature, despite the comment framing it that way — the comparison is simply absent from the function body for all callers), any path relying on `ensure_match_transaction_info` for state-root integrity will silently accept a corrupted/incorrect state root as long as the write set and events happen to match. This is a proof/commit-binding failure of exactly the class targeted by the state-integrity gate: an authenticated commitment (`state_checkpoint_hash`) is not actually verified against the locally-computed value before the output is treated as validated, which is a "wrong accumulator/state root accepted as valid" and "hard-fork-only divergence during ... replay ... verification" scenario.

### Likelihood Explanation
This requires a pre-existing divergence between the locally computed state tree root and the expected `state_checkpoint_hash` while the write set bytes and events remain identical — e.g., a bug in state-tree/Jellyfish-Merkle construction, or a discrepancy introduced by an unrelated storage/replay bug elsewhere in the pipeline. The check itself does not create the divergence; it removes a safety net that should catch such divergences. I could not fully trace whether other layers of the executor pipeline (e.g., `DoStateCheckpoint`/`DoLedgerUpdate`, which do compute and hash `state_checkpoint_hash` when constructing `TransactionInfo` during normal execution: [4](#0-3) ) independently enforce this invariant elsewhere in the state-sync/chunk-apply path outside of `ensure_match_transaction_info`, so I cannot conclusively state that a real state root divergence would go completely undetected end-to-end on mainnet today. This uncertainty, combined with the explicit author TODO gating full fix on the not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature, means the current practical impact on mainnet is unclear without further tracing of every consumer of this function (in particular the state-sync chunk executor, which I could not fully read due to index truncation).

### Recommendation
Add explicit `ensure!` checks in `ensure_match_transaction_info` comparing `self`-derived state/hot-state checkpoint roots (as computed by the executor for this transaction) against `txn_info.state_checkpoint_hash()` and `txn_info.hot_state_checkpoint_hash()` (for V1), independent of the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag, so that replay/verification tooling cannot report success when the authenticated state root diverges from local re-execution.

### Proof of Concept
Not independently reproducible from static analysis alone: exploiting this gap requires an actual state-tree computation divergence (a separate root cause) that produces identical write-set bytes/events but a different Merkle state root, at which point `ensure_match_transaction_info` (as shown above) would pass despite the mismatch — confirmed statically by the absence of any `state_checkpoint_hash` comparison in the function body and the author's own TODO comment acknowledging this exact scenario. I was not able to fully verify whether the state-sync chunk-executor pipeline has redundant validation elsewhere that would independently catch such a divergence before commit, due to index size limits on `execution/executor/src/chunk_executor/mod.rs`; a Devin session with full repository access would be needed to confirm whether this is the only integrity gate on that path.

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

**File:** types/src/transaction/mod.rs (L2336-2341)
```rust
    pub fn state_checkpoint_hash(&self) -> Option<HashValue> {
        match self {
            Self::V0(v) => v.state_checkpoint_hash,
            Self::V1(v) => v.state_checkpoint_hash,
        }
    }
```

**File:** storage/db-tool/src/replay_on_archive.rs (L388-406)
```rust
        for idx in 0..cur_txns.len() {
            let version = *current_version;
            *current_version += 1;

            if let Err(err) = executed_outputs[idx].ensure_match_transaction_info(
                version,
                &expected_txn_infos[idx],
                Some(&expected_writesets[idx]),
                Some(&expected_events[idx]),
            ) {
                cur_txns.drain(0..idx + 1);
                cur_persisted_aux_info.drain(0..idx + 1);
                expected_txn_infos.drain(0..idx + 1);
                expected_events.drain(0..idx + 1);
                expected_writesets.drain(0..idx + 1);

                return Ok(Some(err));
            }
        }
```

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
