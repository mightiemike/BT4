I'll investigate the areas mentioned (write-set conversion, transaction info construction, storage commit, accumulators, Merkle proofs, restore flows) to check for genuine integrity issues before concluding.This "trading-native" TODO comment inside `ensure_match_transaction_info()` looks like a promising, code-documented gap in proof verification. Let me investigate it further to determine root cause, callers, and whether it's really unprotected.### Title
`TransactionOutput::ensure_match_transaction_info()` skips state-checkpoint hash verification, letting `db-tool replay-on-archive` accept executions with a wrong state root - (File: `types/src/transaction/mod.rs`)

### Summary
The replay-verification path (`aptos db-tool replay-on-archive`) is supposed to prove that re-executing an archived transaction against the VM produces the exact same result that was originally authenticated and committed to the ledger. The comparison function it relies on, `TransactionOutput::ensure_match_transaction_info()`, checks status, gas, write-set hash and event-root hash, but never checks the transaction's `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the recomputed values. This is called out directly in a TODO left in the code, confirming it is a known, unresolved gap rather than an intentional design choice.

### Finding Description
`ensure_match_transaction_info()` is the sole correctness gate used by the archive replay verifier: [1](#0-0) 

It validates `status`, `gas_used`, the write-set hash against `state_change_hash`, and the event-root hash — but the function returns `Ok(())` without ever comparing `txn_info.ensure_state_checkpoint_hash()` (or the V1 `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against a locally-recomputed state root for checkpoint transactions. The comment in the code explicitly documents this: [2](#0-1) 

The only caller of this function is `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify`, which drives `aptos db-tool replay-on-archive`, an authenticated verification tool used to validate that a locally-executed replay of archived transactions matches the canonically committed ledger: [3](#0-2) 

Because the state-checkpoint hash (the Merkle root binding the entire account/resource state at a checkpoint version, and, when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, the hot-state and native-position state roots) is never checked here, this tool can report a "successful" replay-verify even when the locally computed state tree diverges from the archived/canonical one — i.e., a wrong state root is accepted as matching, precisely in the accumulator/proof-binding category this scan is targeting.

### Impact Explanation
`replay-on-archive` is an authenticated-replay/integrity tool used to detect state divergence (including hard-fork-only divergence) between different execution engines/versions replaying the same historical transaction stream. Its entire purpose is to catch a corrupted or drifted state root before it's trusted. Because the state/hot-state/position-state checkpoint hashes are excluded from the comparison, this specific gate is blind to exactly the class of bug it's meant to catch: a state root that differs from the correct VM result at a checkpoint boundary would pass verification silently. This directly matches "Wrong accumulator root ... or state proof accepted as valid" and "Hard-fork-only divergence during ... replay ... verification" from the impact list.

### Likelihood Explanation
Likelihood is Low: no unprivileged party can trigger this from the live consensus/execution path — the underlying storage commit path (`state_merkle_batch_committer.rs`, `state_snapshot_committer.rs`, JMT/SMT root equality asserts, etc.) still enforces state-root correctness for the actual canonical database. The gap only affects the offline/operator-run `replay-on-archive` tool's self-check, so an actual state divergence (e.g. from a VM/gas-schedule bug or a hard-fork mismatch) would go undetected by this specific verification pass rather than being directly exploitable by an attacker to corrupt mainnet state.

### Recommendation
In `ensure_match_transaction_info()`, recompute the expected state-checkpoint hash (and, when applicable, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) for checkpoint transactions and `ensure!` it matches `txn_info`'s corresponding field(s), removing the TODO instead of leaving the check unimplemented, so `replay-on-archive` cannot report success while state roots diverge.

### Proof of Concept
1. Archive a transaction range that includes a state-checkpoint transaction.
2. Run `aptos db-tool replay-on-archive` against a target DB where the local execution engine (or gas/feature config) produces a different state tree at that checkpoint version but coincidentally the same write-set hash/event-root/gas/status for the individual transactions replayed (e.g., a difference only manifesting in aggregated state, like a different `usage`/pruning-affecting field, or an issue confined to `hot_state`/`position_state` computation gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`).
3. `Verifier::execute_and_verify` calls `ensure_match_transaction_info()` [4](#0-3)  which returns `Ok(())` despite the state_checkpoint_hash/hot_state_checkpoint_hash/position_state_checkpoint_hash never being compared, so the tool reports the replay as verified even though the state root diverges from the archived one.

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
