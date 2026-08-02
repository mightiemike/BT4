Based on my investigation, I found a concrete state-integrity analog with local proof in the codebase's own transaction-info verification logic.

### Title
`TransactionOutput::ensure_match_transaction_info` skips state-checkpoint hash comparison, letting divergent state roots pass replay/restore verification as valid - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the core integrity check used by chunk-execution verification, backup restore, replay-verify tooling, and the Aptos debugger to confirm that a locally re-executed transaction matches the authenticated `TransactionInfo` recorded on-chain. The function only checks transaction status, gas used, write-set hash, and event-root hash — it deliberately omits comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, which is exactly the field that authenticates the Sparse-Merkle/Jellyfish state root. This is the Aptos-native analog of the SIZE bug class: an authenticated commitment field (bid pubkey/commitment validity in the original report; here the state root) is left unchecked in the code path whose entire purpose is to validate correctness, silently accepting a wrong value as valid.

### Finding Description
`ensure_match_transaction_info` is defined at [1](#0-0)  and explicitly checks only status, gas, and write-set hash. The event root hash is checked immediately after, and then the function returns `Ok(())` without ever comparing `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` against locally computed values. The code even contains a self-acknowledging TODO stating the gap: [2](#0-1) .

This function is the sole correctness gate in several security-relevant call sites:
- Chunk executor replay-verification during state sync / backup restore (`verify_execution`), which calls it per-transaction to decide whether locally re-executed output matches the trusted `TransactionInfo` stream: [3](#0-2) .
- The archive replay-verify tool (`db-tool`'s `replay_on_archive`), used specifically to detect divergence between VM execution and historically committed transaction infos.
- The Aptos debugger and CLI replay commands, used by operators/auditors to confirm a given historical execution is correct.

Because `state_checkpoint_hash` (and its V1 siblings `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) is the hash that binds the entire world-state (all resources/modules) at a checkpoint boundary — as documented on `TransactionInfoV0`/`TransactionInfoV1` [4](#0-3)  — leaving it out of the equality check means the verification path can never detect a state-root divergence, only a write-set-hash divergence. Write-set hash and state root are computed via different code paths (write-set hashing is a straightforward BCS hash of individual writes, whereas the state checkpoint hash is produced by JMT/SMT batch update logic in `DoStateCheckpoint`), so a bug specific to the state-checkpoint construction (e.g., in Jellyfish Merkle batch application, hot-state promotion, or the newer "trading native state roots" logic referenced by the TODO) would produce a wrong `state_checkpoint_hash` while the write-set hash still matches, and this tool would report success.

### Impact Explanation
This breaks the "authenticated proof/response bound to the correct root" invariant required by the state-integrity gate: replay-verify and restore-verification flows can accept a chunk/transaction as correctly replayed even though the recomputed state root diverges from the authenticated on-chain value. This is precisely the class of "hard-fork-only divergence during commit, replay, restore, or proof verification" that the gate calls out as high/critical — a bug in state-checkpoint-hash computation (JMT root construction, hot-state root, or the new "position state checkpoint hash") would silently pass all of the integrity tooling meant to catch exactly this kind of divergence, delaying or masking detection of ledger corruption that is used to gate production releases and post-incident forensics.

### Likelihood Explanation
The check is unconditionally weaker than it should be for every call — no attacker interaction is required, and the flaw exists in every use of `ensure_match_transaction_info` (state-sync chunk restore verification, backup-restore replay verification, archive replay-verify tooling, debugger). Because the state-checkpoint hash is only produced "periodically" (`Option<HashValue>`, per the field docs) and computed through a materially different logic path than the write-set hash, any regression introduced into `DoStateCheckpoint`'s state-root or hot-state/position-state-root logic would go undetected by exactly the mechanism that's supposed to catch it — this is a real, currently-acknowledged (via the in-code TODO) gap, not a hypothetical one.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when both the expected and computed values are known/present) against the corresponding locally computed checkpoint hashes, rather than only comparing the write-set and event-root hashes. At minimum, gate the currently-silent gap behind a hard failure rather than a TODO before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, since new state-root logic is exactly the kind of change this verification exists to protect against.

### Proof of Concept
1. Introduce (or trigger via a real bug) a discrepancy in state-checkpoint-hash computation only — e.g., a bug in `DoStateCheckpoint`'s JMT batch update or hot-state promotion logic that changes the computed `state_checkpoint_hash`/`hot_state_checkpoint_hash` for a block while leaving the transaction's write set and events unchanged.
2. Run chunk-executor verify_execution (state sync verify mode) or `db-tool replay-on-archive verify`, both of which call `ensure_match_transaction_info` [5](#0-4) .
3. Observe that verification returns `Ok(())` for the affected transaction because status, gas, write-set hash, and event-root hash all still match — the state-root divergence is never checked, so the tool reports the replay as successfully verified despite the state commitment being wrong.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2178)
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
```

**File:** types/src/transaction/mod.rs (L2197-2203)
```rust
        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
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

**File:** execution/executor/src/chunk_executor/mod.rs (L684-706)
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
```
