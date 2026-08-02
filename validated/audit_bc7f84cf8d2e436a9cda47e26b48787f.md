## State-Integrity Analysis: Position/Trading-Native Checkpoint Hash Bypass in Replay Verification

### Title
Replay-verification comparator ignores state/hot-state/position checkpoint hashes, allowing divergent committed state to pass as "verified" - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info`, the function used to validate that a locally-produced `TransactionOutput` matches the authenticated `TransactionInfo` fetched from an archive/peer, checks transaction status, gas used, write-set hash, and event root hash — but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. [1](#0-0) 

### Finding Description
The comparator's own comment documents the gap: [2](#0-1) 

It states that this comparator "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution," and that checkpoint hashes must be validated "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`."

This means that the checkpoint hash fields carried in `TransactionInfoV0`/`TransactionInfoV1` — `state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash` — are part of the authenticated, accumulator-committed `TransactionInfo` (hashed into the transaction accumulator, per `assemble_transaction_infos` in `execution/executor/src/workflow/do_ledger_update.rs`), but the only consumer-side comparator that cross-checks a locally computed `TransactionOutput` against an authenticated `TransactionInfo` skips exactly these fields. [3](#0-2) 

The `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag (`types/src/on_chain_config/aptos_features.rs`, referenced also in `storage/aptosdb/src/db/aptosdb_reader.rs`) gates whether the new "trading-native"/position state Merkle root is actually computed and validated at all in this code path, confirming that this is a partially-built feature where the state-root binding for a new state category (position state) is not yet enforced end-to-end.

### Impact Explanation
If replay-verify tooling (`db-tool`'s `replay_on_archive`, and any downstream consumer of `ensure_match_transaction_info`) is used as a correctness oracle for detecting divergence between a node's local re-execution and the authenticated chain history, this gap means a state root corruption/divergence in the state Merkle tree, hot-state tree, or the new position-state tree can go undetected by replay verification, even though the write set and event root match. This directly maps to the "wrong accumulator root / proof accepted as valid" and "hard-fork-only divergence during replay" impact categories: a bug that corrupts checkpoint-hash computation (e.g., in the position-state committer or hot-state pipeline) would not be caught by this comparator, undermining a primary detection mechanism relied upon to catch consensus/state divergence before it propagates.

### Likelihood Explanation
Likelihood is Low: this requires (a) an underlying bug or divergence in checkpoint-hash computation to exist, and (b) reliance on `ensure_match_transaction_info` (or `replay_on_archive`) as the sole detection mechanism. The comment indicates the team is aware of the gap and has explicitly gated the new position/trading-native root computation behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS` specifically because this validation is incomplete, meaning this is a self-acknowledged, in-progress limitation rather than a silently exploitable bug already reachable on mainnet — the feature flag suggests the trading-native root logic is not yet enabled in production. The impact would only materialize on mainnet once that flag is turned on while this comparator gap remains unfixed.

### Recommendation
Extend `ensure_match_transaction_info` to also validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally recomputed values (when the transaction is a checkpoint boundary) before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in production, exactly as the existing TODO comment states. Track this as a release-blocking item gated on the feature flag rollout, and add integration tests in the replay-verify pipeline that intentionally corrupt a checkpoint hash to confirm detection.

### Proof of Concept
Not applicable as a live exploit — this is a self-documented verification gap in `ensure_match_transaction_info`: [4](#0-3) 
A conceptual PoC: construct a `TransactionOutput` whose `write_set` and `events` match the authenticated `TransactionInfo`, but whose actual post-state (as would be reflected in `state_checkpoint_hash`/`position_state_checkpoint_hash`) diverges from the value embedded in the `TransactionInfo` fetched from archive; `ensure_match_transaction_info` returns `Ok(())` regardless, since it never reads `txn_info.state_checkpoint_hash()` or `position_state_checkpoint_hash()` for comparison. I was not able to fully trace whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is currently enabled on mainnet or only in development, since the flag's activation status is determined by on-chain governance data not visible in the indexed code — this should be confirmed before treating the impact as immediately live.

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L95-123)
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
                let txn_info_hash = txn_info.hash();
                (txn_info, txn_info_hash)
```
