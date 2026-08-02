## Finding

### Title
Replay-verify skips validating the state checkpoint (and hot-state / position-state) root hashes, letting corrupted committed state pass verification undetected - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by the `db-tool replay_on_archive` verifier to confirm that a locally re-executed transaction output matches the authenticated `TransactionInfo` that was actually committed to the ledger (and covered by the transaction accumulator / `LedgerInfo` signatures). The function checks `status`, `gas_used`, `state_change_hash` (write-set hash) and `event_root_hash`, but it never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. A divergence in state-root computation is therefore invisible to replay-verify, which is exactly the state-proof-integrity property this tool exists to guarantee.

### Finding Description
`ensure_match_transaction_info` explicitly documents the gap in its own code: [1](#0-0) 

It compares only four fields — status, gas used, the write-set hash against `state_change_hash`, and the event root hash — and then returns `Ok(())` without ever inspecting `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()`: [2](#0-1) 

The only caller of this function is the `replay_on_archive` verifier, which re-executes historical transactions and calls `ensure_match_transaction_info` per transaction to decide pass/fail: [3](#0-2) 

`state_checkpoint_hash` is the root hash of the Sparse/Jellyfish Merkle state tree at the end of a (checkpoint) transaction — the value that downstream proof verification (`TransactionInfoWithProof`, state snapshot restore, light-client consistency proofs) treats as authoritative for the committed world state: [4](#0-3) 

Because this root is skipped, if the VM/executor's state-application logic (or any of the newer state-root paths added in this fork — hot state, "trading-native" position state) diverges from the value actually recorded on-chain, `execute_and_verify` in `replay_on_archive.rs` will still report success as long as the write set and events happen to match. The write set matching is not sufficient: `state_change_hash` only proves the write set produced by the VM's output is unchanged before storage — it does not prove that applying that write set onto the correct base state produces the same root that was actually committed and signed by validators.

### Impact Explanation
This breaks a proof/commit-integrity invariant explicitly called out in the required impacts: "Committed state that differs from the correct VM result or corrupts durable ledger data" and "Hard-fork-only divergence during commit, replay, restore, or proof verification" must be detectable. Replay-verify is the tool operators and auditors use to confirm that an independently-replayed history matches the authenticated on-chain state root. With this gap, a state-root computation bug (e.g., introduced by the "trading-native" state root work referenced in the TODO) can silently corrupt the durable state root while replay-verify reports full success, masking a consensus-state divergence that should have been flagged as a hard-fork-class bug.

### Likelihood Explanation
This requires no attacker action — it is a latent gap in an already-existing verification codepath, self-documented by the maintainers via the `TODO(trading-native)` comment, and it is unconditionally reachable any time `replay_on_archive` is run (its only caller). The likelihood of the bug being *triggered* depends on a separate state-root computation defect elsewhere, but the verification gap itself is unconditional and currently shipped.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the locally recomputed values before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, as the TODO itself recommends, so replay-verify cannot report success while the authenticated state root diverges from local execution.

### Proof of Concept
1. Run `db-tool replay-on-archive` over a version range containing a checkpoint transaction.
2. Introduce (or trigger, via an existing latent bug in the hot-state/position-state root computation) a divergence such that the locally computed `state_checkpoint_hash` differs from the persisted `TransactionInfo.state_checkpoint_hash`, while the write set and events remain identical.
3. Observe that `execute_and_verify` → `ensure_match_transaction_info` returns `Ok(())` for that transaction because only `status`, `gas_used`, `state_change_hash`, and `event_root_hash` are checked, at `types/src/transaction/mod.rs:2159-2203`.
4. The replay-verify run completes with zero failures despite the state root diverging, demonstrating that a corrupted/incorrect committed state root is accepted as valid by the tool.

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
