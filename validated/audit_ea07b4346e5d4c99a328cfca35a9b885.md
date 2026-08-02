Based on my investigation, I found a genuine analog in the replay/proof-verification path: `TransactionOutput::ensure_match_transaction_info` in `types/src/transaction/mod.rs` is missing state-root comparisons, and this function is the sole verification gate used by the `db-tool replay-on-archive` tool.

### Title
`TransactionOutput::ensure_match_transaction_info` never verifies state/hot-state/position checkpoint hashes, letting replay-verify accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`ensure_match_transaction_info` is the function used by archival replay-verification tooling to confirm that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` recorded on-chain/in backups. It checks status, gas, write-set hash, and event-root hash, but it explicitly skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — a gap the code itself documents with a `TODO(trading-native)` comment.

### Finding Description
`ensure_match_transaction_info` computes and compares only the write-set hash and event-root hash against the target `TransactionInfo`: [1](#0-0) 

Immediately after, the code acknowledges that it does not validate the state checkpoint hashes at all: [2](#0-1) 

This function is the only correctness check invoked by `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which drives `Opt::run` (the `replay-verify` / `replay-on-archive` CLI subcommand) to re-execute historical transactions and confirm they reproduce the exact ledger state recorded on-chain: [3](#0-2) 

Because `state_checkpoint_hash` (the Sparse-Merkle-Tree root summarizing the world state after the transaction) and its `hot_state`/`position_state` variants are never compared, this tool — whose entire purpose is to catch state divergence between two independent VM executions of the same recorded transaction history — cannot detect a mismatch in the actual state root. It can only detect divergence in the write set's *hash* and event hashes, not in the resulting Merkle state commitment itself. The same `TransactionInfo` type stores this hash as the authenticated summary of world-state at that version, per the field comment: [4](#0-3) 

### Impact Explanation
If VM execution logic diverges from the historically committed one (e.g., a subtle non-determinism bug, an unintended change in how `DoStateCheckpoint`/JMT hashing is computed, or a bug introduced during a future upgrade path such as the referenced `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/position-state feature), `replay_on_archive` would still report a clean pass as long as the write-set bytes and events line up, even though the resulting state root differs from the authenticated one. This is exactly the class of "hard-fork-only divergence during commit, replay, or proof verification" that should be caught by ledger-replay tooling; instead it is silently masked, undermining the primary safety net operators rely on to detect consensus-breaking state-computation bugs before/after they reach mainnet.

### Likelihood Explanation
Likelihood is low under normal conditions (write-set hash equality is a reasonably strong signal correlated with state equality), but it is not a guarantee: a hash mismatch could theoretically be introduced elsewhere in the checkpoint/hot-state materialization pipeline (`DoStateCheckpoint`) without touching the write set that's hashed into `state_change_hash`, since the state checkpoint hash is computed from applying the write set against prior state rather than being derivable purely from the write set bytes. The gap is also explicitly flagged in-repo as a known, not-yet-fixed limitation blocking a feature rollout, indicating the maintainers are aware the check is currently insufficient.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the recomputed values (when both sides have them present), so that `replay_on_archive` and any other caller (`aptos-debugger`, `cli/src/commands.rs`) cannot report a clean replay when the authenticated state root actually diverges.

### Proof of Concept
1. Run `db-tool replay-on-archive` (or the equivalent `TransactionReplayer`/CLI path) over a backup range.
2. Craft (or trigger via a hypothetical VM/state-checkpoint bug) a transaction whose replayed execution produces an identical `WriteSet` and event list, but whose resulting Jellyfish Merkle root (`state_checkpoint_hash`) differs from the one recorded in the archived `TransactionInfo` — for instance if `DoStateCheckpoint` applied the same write set against a different base state root or via a hashing regression.
3. `execute_and_verify` calls `ensure_match_transaction_info`, which only checks status/gas/write-set-hash/event-root-hash and passes.
4. The tool reports the range as successfully verified even though the resulting authenticated state root diverges from what local execution actually computed.

---
Note: this is a self-acknowledged code gap (via the in-repo `TODO(trading-native)` comment) rather than a novel finding I independently confirmed as exploitable on current mainnet — the checkpoint-hash fields it references (`hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) are tied to features (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that may not be fully active yet [5](#0-4) . You should weigh this context when deciding how to prioritize it.

### Citations

**File:** types/src/transaction/mod.rs (L2168-2203)
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
```

**File:** types/src/transaction/mod.rs (L2405-2413)
```rust
    /// The hash value summarizing all changes caused to the world state by this transaction.
    /// i.e. hash of the output write set.
    state_change_hash: HashValue,

    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,

```

**File:** storage/db-tool/src/replay_on_archive.rs (L392-406)
```rust
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
