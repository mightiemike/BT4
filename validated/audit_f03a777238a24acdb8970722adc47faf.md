Based on my investigation, I found a genuine local integrity gap in the replay-verification path, independent of the original ETH-withdrawal bug class, but matching the same "shape": a step that is expected to fully validate a critical asset (there, funds; here, the authenticated ledger state) is silently missing a check.

### Title
`ensure_match_transaction_info` omits state/hot-state/position checkpoint hash verification, allowing replay-verify tooling to accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verification tooling to confirm that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` stored on-chain. It checks status, gas used, write-set hash, and event root hash, but it deliberately does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that authenticate the actual state tree root produced by a transaction. [1](#0-0) 

### Finding Description
The function's own inline TODO acknowledges the gap explicitly: [2](#0-1) 

It validates:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set_hash` vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()` [3](#0-2) 

But `TransactionInfo` (both V0 and V1) also carries `state_checkpoint_hash`, and V1 additionally carries `hot_state_checkpoint_hash` and `position_state_checkpoint_hash` — none of which are compared here. [4](#0-3) 

This function is the sole correctness check used by `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes archived transactions with the VM and calls `ensure_match_transaction_info` to decide whether replay succeeded: [5](#0-4) 

Because write-set hash and event root hash are checked but the state-checkpoint-related hashes are not, a re-executed transaction whose resulting **state tree root** (the authenticated summary of the ledger's Jellyfish Merkle / hot-state / position state) diverges from the originally committed root — while still producing an identical write-set hash and events (e.g., due to a state-checkpoint/JMT construction bug, a hot-state materialization bug, or a `position_state_checkpoint_hash` computation bug elsewhere in the executor) — would be reported by `replay_on_archive` as a **successful, matching replay**, even though the committed ledger state root has silently diverged.

### Impact Explanation
This breaks the state-proof/commitment integrity invariant this campaign is scoped to protect: "Committed state that differs from the correct VM result" and "replay paths ... must not reinterpret committed data into a different ledger state." The whole purpose of `replay_on_archive` is to independently attest that historical execution reproduces the authenticated ledger; if the state-checkpoint hash fields are excluded from the comparison, a real divergence in the committed state root (which downstream feeds the ledger's accumulator/proof system) can pass verification undetected. This directly undermines confidence in state-root correctness for anyone relying on replay-verify as an integrity oracle (auditors, node operators doing fork/divergence detection, disaster-recovery validation).

### Likelihood Explanation
The gap is not a theoretical read — it is explicitly called out by the authors as a known limitation ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), meaning the codebase itself documents that this verification path is presently insufficient for the position-state-root feature. The trigger only requires a bug in state-checkpoint/hot-state/position-state-root computation elsewhere in the executor (independent of write-set/event correctness) to go unnoticed by this specific tool.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, and where present, `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`, against values recomputed from local execution before enabling any state-root-dependent feature (e.g. `COMPUTE_TRADING_NATIVE_STATE_ROOTS`), so that `replay_on_archive` and other callers (`aptos-debugger`, `aptos-move/cli`) cannot report a false-positive match when the authenticated state root diverges.

### Proof of Concept
Not independently executable without a concrete state-checkpoint-computation bug to trigger the divergence; the code-level proof is the structural fact that `ensure_match_transaction_info` (lines 2139–2204) never reads `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`, while `execute_and_verify` in `replay_on_archive.rs` (lines 392–405) treats an `Ok(())` from this function as a full replay match.

**Caveat**: I could not fully trace whether other callers (`aptos-debugger`, `aptos-move/cli`) or an upstream commit path perform an independent state-checkpoint-hash check elsewhere before or after calling this function, since I ran out of tool budget before reading those call sites' full context. If such a check exists elsewhere in those specific call paths, the practical exposure would be narrower than described (limited to `replay_on_archive` only).

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

**File:** types/src/transaction/mod.rs (L2261-2284)
```rust
    #[builder(finish_fn = build)]
    pub fn builder_v1(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        hot_state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
        position_state_checkpoint_hash: Option<HashValue>,
    ) -> Self {
        Self::V1(TransactionInfoV1::new(
            transaction_hash,
            state_change_hash,
            event_root_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            gas_used,
            status,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
        ))
    }
```

**File:** storage/db-tool/src/replay_on_archive.rs (L392-405)
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
```
