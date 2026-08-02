This confirms the finding: the `ensure_match_transaction_info` function is the sole correctness check used by `db-tool`'s `replay_on_archive` verifier, and it deliberately omits validation of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, as documented in its own TODO comment.

### Title
Replay-verify integrity check silently skips checkpoint/state-root validation, masking authenticated-state divergence - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative comparator used by the `replay_on_archive` tool (and any other consumer relying on it) to confirm that locally re-executed transaction output matches the authenticated `TransactionInfo` recorded on-chain/in backups. The function validates status, gas used, write-set hash, and event-root hash, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents as a known, unresolved TODO tied to enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

### Finding Description [1](#0-0)  shows `ensure_match_transaction_info` performing four checks — status, gas, write-set hash (`state_change_hash`), and event root hash — then returning `Ok(())` without ever comparing `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` against values derived from local execution. The comment directly above the `Ok(())` states this is a known gap:

> "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is the exact integrity gate used in `storage/db-tool/src/replay_on_archive.rs`: `execute_and_verify` re-executes each transaction locally with `AptosVMBlockExecutor`, then calls `ensure_match_transaction_info` against the backup-sourced `expected_txn_infos[idx]` [2](#0-1) . Because the state/hot-state/position checkpoint hashes are never compared, a divergence in the Sparse Merkle Tree root, hot-state root, or the newly introduced "trading-native" position state root (gated by `compute_trading_native_state_roots` in [3](#0-2) ) between the locally computed state and the authenticated on-chain/backup value will pass replay-verify undetected, as long as the write-set and event hashes still match.

### Impact Explanation
This breaks the state-commitment/proof-integrity invariant that authenticated replay tooling must detect any divergence between locally computed ledger state and the authenticated commitment. Replay-verify is one of the primary safety nets used to catch non-determinism, storage bugs, or consensus-vs-execution divergence across node versions (including hard forks) before/after upgrades. A bug that corrupts only the state checkpoint root, hot-state root, or position-state root (while leaving the write set and event hashes intact — plausible since these roots are derived from the write set via a separate Merkle-tree computation) would go completely unnoticed by this tool, giving false confidence that historical execution is deterministic and correct. This is a high-severity gap in proof/commitment verification tooling, matching "hard-fork-only divergence during commit, replay, restore, or proof verification" and "wrong accumulator root ... state proof accepted as valid" in spirit — here the check that would have caught the wrong root is missing entirely.

### Likelihood Explanation
This is not a hypothetical: the code is unconditionally reachable any time `replay_on_archive` or any other caller invokes `ensure_match_transaction_info` (currently the only two call sites are `replay_on_archive.rs` and the definition site itself), so the gap is exercised on every replay-verify run today, for every transaction, regardless of feature flags. It requires no attacker action — it is a latent, self-acknowledged blind spot in an integrity-checking tool that operators rely on to catch state-root corruption bugs.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in either the actual or expected `TransactionInfo`) against locally recomputed values before returning `Ok(())`, exactly as the existing TODO comment recommends, prior to (or as a prerequisite for) enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or any feature that depends on these roots for ledger correctness.

### Proof of Concept
1. Run `replay_on_archive` (or any caller of `ensure_match_transaction_info`) against a backup/archive where a transaction's write set and events are unchanged, but the persisted `TransactionInfo` carries a `state_checkpoint_hash` (or `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) that does not match what re-executing the write set against the correct pre-state would produce (e.g., simulate a Jellyfish Merkle Tree corruption bug that only affects internal-node hashing, not the write-set/event hashes).
2. Observe that `ensure_match_transaction_info` at [4](#0-3)  returns `Ok(())` because it never inspects the checkpoint-hash fields.
3. `execute_and_verify` in [5](#0-4)  therefore records no error for this transaction, and the replay-verify job reports success despite an authenticated state-root mismatch.

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

**File:** types/src/block_executor/config.rs (L180-188)
```rust
        // Requires transaction_info_v1 (the root rides in TransactionInfoV1) and
        // hotness_in_epilogue (only the V1 write-set format it enables serializes
        // the native-position extensions; V0 drops them, so output-replay would
        // diverge). Degrades to off if either is missing.
        self.compute_trading_native_state_roots = features
            .is_compute_trading_native_state_roots_enabled()
            && features.is_transaction_info_v1_enabled()
            && features.is_hotness_in_epilogue_enabled();
        self
```
