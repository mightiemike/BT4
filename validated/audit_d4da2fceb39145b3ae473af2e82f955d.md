## Title
`TransactionOutput::ensure_match_transaction_info` skips checkpoint-hash validation, allowing replay/restore to accept a corrupted state root - (File: types/src/transaction/mod.rs)

## Summary
`TransactionOutput::ensure_match_transaction_info` in [1](#0-0)  is the integrity check used by chunk executor / replay-verify tooling to confirm that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` fetched or replayed from storage/state-sync. It checks status, gas used, write-set hash, and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents with a `TODO(trading-native)` comment.

## Finding Description
The function is called from `execution/executor/src/chunk_executor/mod.rs` and `aptos-move/aptos-debugger/src/aptos_debugger.rs`/`aptos-move/cli/src/commands.rs` to validate that replayed/chunk-applied transaction outputs match the `TransactionInfo` that was already committed and covered by a `LedgerInfo` signature (i.e., authenticated data). The comment at [2](#0-1)  states verbatim that this comparator "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution," and that checkpoint hashes must be validated "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`."

`state_checkpoint_hash` (and its V1 siblings `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) is precisely the field that binds a `TransactionInfo` to the resulting state tree/root after the transaction, distinct from the write-set hash. In normal ledger commit, `DoLedgerUpdate::assemble_transaction_infos` in [3](#0-2)  populates these checkpoint hash fields from `StateCheckpointOutput` and feeds them into the accumulator that produces the final ledger root. Skipping their comparison during replay/restore means the check can pass even though the state root computed locally diverges from what was actually committed/authenticated on-chain.

## Impact Explanation
This breaks the "authenticated position state root" invariant described directly in the code: replay/restore verification (`replay_on_archive` and similar db-tool flows, plus any caller of `ensure_match_transaction_info` in the chunk executor and debugger) can silently accept a transaction output whose resulting state-checkpoint/hot-state/position-state root differs from the one actually committed under the signed `LedgerInfo`. In a system meant for authenticated proof binding, this is a state-commitment integrity gap: a divergent hot-state or position-state root produced by a buggy or malicious execution path is not caught, which the code's own author flags as a precondition that must be fixed before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` can be safely enabled.

## Likelihood Explanation
This is a documented, structural gap rather than a speculative one — it is explicitly called out by name in the source (`TODO(trading-native)` comment) as a known missing validation. It only manifests when the state checkpoint/hot-state/position-state hash actually diverges from the write-set-derived result (which write-set hash checking does not cover), and its practical severity is gated by whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and the V1 `TransactionInfo` (with hot-state/position-state fields) are enabled on the target network; the feature flag reference is present in [4](#0-3)  and [5](#0-4) , but I was not able to fully trace, within the remaining tool budget, whether the feature is currently gated off by default in a way that fully neutralizes exploitability today.

## Recommendation
Extend `ensure_match_transaction_info` to also compare `self`-derived (or externally supplied) `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the corresponding fields on `txn_info`, mirroring the existing `write_set_hash`/`event_root_hash` pattern, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or `TRANSACTION_INFO_V1` paths are relied upon for authenticated replay/restore verification.

## Proof of Concept
I could not construct/verify an end-to-end PoC within the available tool budget — specifically I was unable to fully confirm (a) whether `TRANSACTION_INFO_V1`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS` are enabled on mainnet today, and (b) the exact call sites in `chunk_executor/mod.rs` and `aptos_debugger.rs` that consume the boolean result of `ensure_match_transaction_info` to gate acceptance of a chunk/replay. Given the explicit self-documented gap in the code (`TODO(trading-native)` at [2](#0-1) ), this is presented as the strongest local integrity-analog candidate found, but full exploitability confirmation requires a Devin session with terminal/build access to trace feature-flag defaults and call-site handling of the verification result.

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L95-121)
```rust
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

**File:** types/src/on_chain_config/aptos_features.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```

**File:** storage/aptosdb/src/db/aptosdb_reader.rs (L1-1)
```rust

```
