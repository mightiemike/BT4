### Title
`ensure_match_transaction_info` skips checkpoint-hash validation, letting replay-verify accept state/hot-state/position-state root divergence - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated comparison function used to confirm that a locally re-executed transaction output matches the on-chain `TransactionInfo` retrieved from a backup/archive. It checks status, gas, write-set hash, and event-root hash, but explicitly skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the very fields that commit to the Merkle state root and the new "trading-native" position-state root. This function is the sole verification gate used by `storage/db-tool/src/replay_on_archive.rs` (and other replay-consuming tools) to detect divergence between recomputed and committed ledger state.

### Finding Description [1](#0-0) 

The function builds the expected `TransactionStatus`, compares `gas_used`, compares `write_set_hash` against `txn_info.state_change_hash()`, and compares the event root hash — but stops there. The comment directly above the `Ok(())` return states the gap: [2](#0-1) 

This means any divergence in the recomputed `state_checkpoint_hash` (main SMT root), `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` (native "trading" position state root, computed in `execution/executor/src/workflow/do_state_checkpoint.rs`'s `compute_position_checkpoint`) relative to the archived, ledger-committed `TransactionInfo` is silently ignored.

This function is called directly by the replay-verify tool's per-transaction integrity check: [3](#0-2) 

Here, `executed_outputs[idx].ensure_match_transaction_info(...)` is the only per-transaction correctness check performed against the archived, ledger-committed `expected_txn_infos[idx]`. Since the comparator ignores the checkpoint-hash fields, a bug or divergence in state-checkpoint computation (main state SMT, hot-state SMT, or the newer native position-state SMT introduced in `do_state_checkpoint.rs`) during re-execution will not be flagged as a failure, even though the true, authenticated `TransactionInfo` hash (which does cover these fields, per `TransactionInfoV1`'s fields in the same file) differs from what was locally recomputed.

### Impact Explanation
Replay-verify (and any other consumer of `ensure_match_transaction_info`, e.g. `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) is the primary offline tool operators and auditors rely on to detect a divergence between locally executed state and the authenticated, consensus-committed ledger state root. Because the checkpoint-hash fields are excluded from the comparison, a state-root computation bug (e.g. in the new native-position-state logic in `compute_position_checkpoint`, or any hot-state/state-checkpoint regression) can silently pass replay-verification while the locally recomputed state root diverges from the authenticated on-chain root. This defeats the core purpose of replay-verify: catching state-commitment divergence before it becomes a hard-fork-inducing consensus bug, directly matching the "Hard-fork-only divergence during ... replay ... verification" and "authenticated ... proof-bearing responses" impact categories.

### Likelihood Explanation
The gap is not itself attacker-triggered on mainnet consensus — it does not corrupt live consensus state directly — but it is a documented, always-present blind spot in the one function responsible for validating recomputed vs. authenticated checkpoint roots. Any latent bug in the state-checkpoint or position-state computation code path (which is new/evolving, per the surrounding `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature-gated logic) would go undetected by this tool, so the likelihood of the *underlying* class of bug slipping through unnoticed is non-trivial given this is explicitly called out as a known limitation by the code's own authors.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on either side) against the recomputed values before allowing `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (and hot-state root inclusion) to be considered fully verified by replay tooling, as the TODO in the code itself recommends.

### Proof of Concept
Not directly exploitable as a live network attack; the issue is demonstrated by code inspection: `ensure_match_transaction_info` in `types/src/transaction/mod.rs` (lines 2139-2204) never reads `state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` from `txn_info`, and its caller in `storage/db-tool/src/replay_on_archive.rs` (lines 392-406) treats a passing call as full transaction-output verification, including for chunks/epoch boundaries where these fields are the only externally-authenticated commitment to the corresponding state roots.

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
