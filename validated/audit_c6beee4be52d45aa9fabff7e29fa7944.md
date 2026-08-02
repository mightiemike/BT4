### Title
`ensure_match_transaction_info` skips checkpoint-hash checks during replay verification, allowing a divergent state root to pass replay/execution verification - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) is the single-transaction analog of the field validations described in the external report: it is supposed to bind an executed `TransactionOutput` to the authenticated `TransactionInfo` for a given `version` by checking status, gas, write-set hash, and event-root hash. As the in-code TODO comment explicitly documents, it *does not* check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the very fields that authenticate the resulting state Merkle root(s).

### Finding Description
Just as the Solidity report flagged that `Gateway.query()` validated some input fields (`q.to`) but silently skipped others (`dstChainId`, `height`, `slot`, `message`), `ensure_match_transaction_info` validates several derived-hash fields (`state_change_hash` from write set, `event_root_hash` from events, `gas_used`, `status`) but skips the checkpoint-hash fields entirely: [1](#0-0) 

The comment makes the gap explicit:
> "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is called from `execution/executor/src/chunk_executor/mod.rs`'s `verify_execution` path, which is invoked during backup/db-tool replay-verification of historical transactions (`remove_and_replay_epoch` → `verify_execution` → `ensure_match_transaction_info`): [2](#0-1) 

It is also used directly by `aptos-move/aptos-debugger` and `storage/db-tool/src/replay_on_archive.rs` mismatch-reporting tooling, which is the primary consumer named in the TODO comment itself.

### Impact Explanation
Because the checkpoint hashes (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, and especially `position_state_checkpoint_hash` used for the trading-native/position state root) are excluded from the equality check, a locally re-executed transaction whose *state* (not just its write set, events, gas, or status) diverges from the historical/authenticated ledger will still pass `ensure_match_transaction_info` and be reported as a successful replay by tools like `replay_on_archive`. This means proof-bearing checkpoint roots can silently diverge from the correct VM result without being caught by the verification tool designed to catch exactly that divergence — undermining the trustworthiness of replay-verification as an integrity check against historical, authenticated ledger state.

However, this gap is gated behind the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature and its impact is confined to **verification tooling** (debugger/db-tool/replay-verify), not the core commit path used by consensus or normal state-sync chunk application to commit data to the durable ledger. The actual commit-path proof verification (`StateSyncChunkVerifier::verify_chunk_result` → `ledger_update_output.ensure_transaction_infos_match`) is a separate code path not shown to have the same gap, and standard chunk apply/execute flows (`enqueue_chunk_by_execution`/`enqueue_chunk_by_transaction_outputs`) rely on `TransactionListWithProof::verify`/`TransactionOutputListWithProof::verify`, which check write-set/event hashes and hash-chain integrity via the accumulator, not `ensure_match_transaction_info`.

### Likelihood Explanation
Low-to-moderate. The gap is explicitly flagged by the authors themselves as a known, intentional TODO ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), suggesting it is a documented, tracked limitation rather than an accidental oversight, and it is presently only reachable when trading-native/position-state features are being rolled out. It does not affect mainnet's actual state-commitment/consensus integrity today — its blast radius is limited to the accuracy of a specific offline replay-verification tool used to detect divergence, and that tool's own documentation/TODO already discloses the limitation.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and (when `compute_trading_native_state_roots`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled) `position_state_checkpoint_hash` against locally computed values before the feature is enabled on mainnet, exactly as the existing TODO comment specifies. This closes the analog to the external report's missing-input-validation pattern by ensuring all authenticated-root fields carried in `TransactionInfo` are checked, not just a subset.

### Proof of Concept
Not applicable as an exploitable PoC against consensus/mainnet commit integrity — no local invariant break was found in the actual commit-time proof-verification code path (`TransactionOutputListWithProof::verify`, `StateSyncChunkVerifier::verify_chunk_result`, `ensure_transaction_infos_match`), which independently validate write-set/event hashes and chain hashes via the accumulator. The finding here is scoped to `ensure_match_transaction_info`'s incomplete field coverage, confirmed by the author's own TODO comment at [3](#0-2)  and its call sites in replay/debugger tooling. I was unable to construct a PoC demonstrating an actual mainnet state-commitment corruption from this gap within the current investigation; the impact is confined to the completeness of an offline verification tool rather than the on-chain commit path itself.

### Citations

**File:** types/src/transaction/mod.rs (L2159-2204)
```rust
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

**File:** execution/executor/src/chunk_executor/mod.rs (L684-707)
```rust
        // not `zip_eq`, deliberately
        for (version, txn_out, txn_info, write_set, events) in multizip((
            begin_version..end_version,
            &execution_output.to_commit.transaction_outputs,
            transaction_infos.iter(),
            write_sets.iter(),
            event_vecs.iter(),
        )) {
            if let Err(err) = txn_out.ensure_match_transaction_info(
                version,
                txn_info,
                Some(write_set),
                Some(events),
            ) {
                return if verify_execution_mode.is_lazy_quit() {
                    error!("(Not quitting right away.) {}", err);
                    verify_execution_mode.mark_seen_error();
                    Ok(version + 1)
                } else {
                    Err(err)
                };
            }
        }
        Ok(end_version)
```
