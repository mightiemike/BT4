### Title
`TransactionOutput::ensure_match_transaction_info` silently skips checkpoint-hash validation, letting replay-verify tooling accept a divergent state/hot-state/position state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` at [1](#0-0)  checks transaction status, gas used, write-set hash, and event root hash against an authenticated `TransactionInfo`, but never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. The gap is explicitly acknowledged in a TODO comment in the same function: it warns that "replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

### Finding Description
`ensure_match_transaction_info` is the sole correctness oracle used by replay verification to compare freshly re-executed `TransactionOutput`s against the historical, ledger-committed `TransactionInfo`. It is called directly from `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify` at [2](#0-1) , which is the verification mechanism used to detect state divergence between committed history and local re-execution (also exposed via `replay_verify.rs`/`ReplayVerifyCoordinator`).

The function only validates:
- execution status
- gas used
- write-set hash (`state_change_hash`)
- event root hash

It never validates `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that are populated in `TransactionInfoV1`/`TransactionInfoV0` at commit time in `assemble_transaction_infos` ( [3](#0-2) ) and are the values actually included in the transaction-accumulator leaf hash that consensus and light clients trust. Since these checkpoint hashes are the state roots committed to the accumulator (and therefore hard-fork-relevant proof material), silently omitting them from the replay comparator means a state-root divergence — including one caused by a bug in state/hot-state/position-state-root computation — will not be flagged by replay-verify, even though the write-set/events/status compare identical.

### Impact Explanation
Replay-verify (`db-tool replay-on-archive` / `replay-verify`) is one of the primary tools operators and Aptos Labs use to confirm that archived, ledger-committed history is reproducible by re-execution and therefore trustworthy. Because the state-checkpoint hashes (the actual Merkle roots of world state, hot state, and the "native trading" position state, all of which are hashed into the committed `TransactionInfo` leaf and hence into the accumulator/ledger-info root) are excluded from the comparison, a bug in state-root computation (e.g., in `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` logic) that produces a wrong root would go undetected by this tool. This is precisely a "committed state differs from correct VM result, undetected by replay/proof verification" scenario in the state-integrity gate, since the primary automated safety net for catching such divergence is blind to it.

### Likelihood Explanation
The TODO comment itself confirms this is a known, currently-live gap in the shipped code (not hypothetical), and the affected fields are gated behind feature flags (`TRANSACTION_INFO_V1`, `HOT_STATE_ROOT_IN_TXN_INFO`, `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that are under active development in this fork per the feature-flag comments at [4](#0-3) . The comment explicitly instructs "validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`," indicating the feature is intended to be enabled while this validation gap still exists unless fixed first — i.e., the unsafe window is real and anticipated by the code's own authors.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` between the freshly computed checkpoint output and `txn_info`, gated appropriately (e.g., only comparing hashes that are `Some` per the active feature set) before `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` are enabled on mainnet.

### Proof of Concept
Not independently constructible from the indexed code alone: reproducing the divergence requires driving state/hot-state/position-state root computation to differ from a historically committed root and observing that `replay_on_archive`'s `execute_and_verify` ( [5](#0-4) ) reports success. The code-level proof of the gap is the TODO comment plus the field list omitted from the comparator body, both cited above; full exploitation requires the position/hot-state feature flags to be enabled, which per the config comments is not yet the case on mainnet — this affects the reliability of the *safety net*, not immediate on-chain state, so treat likelihood/impact as conditional on those flags being turned on.

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

**File:** types/src/on_chain_config/aptos_features.rs (L203-209)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
    /// When enabled, execution populates `TransactionInfoV1`'s hot state root hash, so it
    /// is committed to the ledger accumulator. Requires `TRANSACTION_INFO_V1`.
    HOT_STATE_ROOT_IN_TXN_INFO = 123,
```
