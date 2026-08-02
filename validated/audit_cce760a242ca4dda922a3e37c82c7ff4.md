### Title
`ensure_match_transaction_info` skips checkpoint-hash comparison, letting replay-verify tooling accept a divergent state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function `db-tool`'s `replay-verify` (`storage/db-tool/src/replay_on_archive.rs`) uses to decide whether a locally re-executed transaction matches the authenticated, on-chain `TransactionInfo`. It checks status, `gas_used`, the write-set hash (`state_change_hash`), and the event root hash, but it deliberately skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, as documented by the `TODO(trading-native)` comment directly above the `Ok(())` return.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates a re-executed `TransactionOutput` against a trusted `TransactionInfo` (typically obtained from an authenticated `TransactionInfoWithProof`/`TransactionListWithProof` chain rooted in a `LedgerInfo`). It compares:
- status vs `txn_info.status()`
- `gas_used`
- `write_set_hash` vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

but never touches `txn_info.state_checkpoint_hash()` (or the V1 fields `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`), even though `TransactionInfo` carries these as the authenticated summary of "the root hash of the Sparse Merkle Tree describing the world state at the end of this transaction" [2](#0-1) . The comment in the code explicitly states the consequence: "replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution" [3](#0-2) .

This function is the sole verification gate in `Verifier::execute_and_verify` in `replay_on_archive.rs`: it re-executes transactions with `AptosVMBlockExecutor`, and if `ensure_match_transaction_info` returns `Ok`, the tool moves on to the next chunk without any other cross-check of the state root [4](#0-3) . The same function is also called from `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs`, both replay/debug tooling paths, not the executor's live commit path.

The gap is directly tied to the new `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag: "When enabled, execution computes the native-position state root at the checkpoint stage and commits it to `TransactionInfoV1`, so it is consensus-verified" [5](#0-4) . Once that flag is turned on, `TransactionInfoV1.position_state_checkpoint_hash` becomes a consensus-committed field that authenticates the native-position (trading) subsystem's state, yet the replay tool that is supposed to detect divergence from this authenticated value silently ignores it.

### Impact Explanation
This is a replay/verification-integrity gap, not a live-commit consensus bug: the executor's actual commit path (accumulator/ledger writes) is unaffected, so it does not directly corrupt the canonical chain state. However, it defeats the specific safety mechanism (`replay-verify`) whose job is to catch divergence between a node's local VM execution and the authenticated on-chain state root, for the state-checkpoint (main Merkle state), hot-state, and — critically — the position (trading-native) state root once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` ships. A discrepancy here (e.g., from a state-root computation bug in the new native-position/trading subsystem, or an execution non-determinism bug) would go undetected by `replay-verify`, giving false assurance during audits/incident investigation of state divergence or a potential fork, which is exactly the class of "hard-fork-only divergence during commit, replay, restore" impact called out in scope.

### Likelihood Explanation
The code path is unconditionally reachable any time `replay-verify` (or the debugger/CLI replay helpers) is run — no privileged access or malicious actor is required, only a discrepancy between the trusted TransactionInfo checkpoint hash and locally computed state. The condition is currently gated in practice by the fact that `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is a new, presumably not-yet-enabled feature flag, and by the low but non-zero likelihood of encountering a genuine state-checkpoint divergence during ordinary replay-verify runs. The bug is self-acknowledged in the code (`TODO`), confirming the maintainers are aware but haven't yet fixed it, and the fix is explicitly deferred to before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`'s locally computed state checkpoint hash(es) against `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()` whenever these are present in the `TransactionInfo`, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (as the TODO already recommends), so that `replay-verify`/debugger tooling cannot silently pass over a genuine state-root divergence.

### Proof of Concept
Not applicable as a runtime exploit against mainnet consensus — this is a verification-tooling gap, demonstrable by: (1) enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, (2) constructing a `TransactionOutput` whose native-position writes are correct in terms of the surface write-set hash/event root but whose actual materialized position state differs from the `position_state_checkpoint_hash` recorded in the trusted `TransactionInfoV1`, and (3) observing `ensure_match_transaction_info` returns `Ok(())` in `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, silently accepting the divergent replay.

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

**File:** types/src/transaction/mod.rs (L2405-2416)
```rust
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

**File:** types/src/on_chain_config/aptos_features.rs (L203-206)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
```
