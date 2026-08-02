Based on the investigation, the strongest local analog to the "stale-price OI delta" bug class — a value used to *validate* correctness that quietly omits part of the state it's supposed to bind to — is a self-acknowledged gap in `TransactionOutput::ensure_match_transaction_info`.

### Title
Transaction-output/TransactionInfo comparator omits state-checkpoint root fields, letting diverged state be accepted as valid during replay/restore - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used to authenticate a locally re-executed `TransactionOutput` against the trusted `TransactionInfo` (obtained via an accumulator/Merkle proof) during replay and verification flows. It checks status, gas used, `state_change_hash` (write-set hash) and `event_root_hash`, but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the actual state-commitment roots produced by `DoStateCheckpoint`.

### Finding Description [1](#0-0) 

The comparator explicitly validates only:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set_hash` vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

It never compares the checkpoint hashes carried in `TransactionInfo` (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash` on `TransactionInfoV1`, see [2](#0-1) ), even though the code contains a TODO acknowledging exactly this: [3](#0-2) 

This is called from replay/verification tooling paths: `execution/executor/src/chunk_executor/mod.rs`, `storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs`. Separately, the real state-checkpoint root computation happens in `DoStateCheckpoint::get_state_checkpoint_hashes`, which does validate against `known_state_checkpoints`/`known_position_state_checkpoints` when they are supplied — [4](#0-3) . However, the TODO comment itself states this only covers cases where those known hashes are threaded through; `ensure_match_transaction_info`, used independently in the tools above, is the comparator that a human/tool would reasonably assume authenticates the full `TransactionInfo`, and it silently drops the checkpoint-root fields.

### Impact Explanation
Under the `trading-native`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature path, a corrupted or incorrectly-computed `position_state_checkpoint_hash` (or hot-state/main-state checkpoint hash, in call sites that don't separately re-validate via `do_state_checkpoint`) would not be caught by this comparator. Tools like `replay_on_archive` exist specifically to catch exactly this class of non-determinism/corruption bug before/after a network upgrade; a false "PASS" from replay-verify while the authenticated position/state root has diverged means a real ledger-corrupting bug could reach or persist on mainnet undetected until a hard fork/consensus split occurs.

### Likelihood Explanation
This requires the `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/hot-state feature to be enabled and a real (separate) computation bug in the position-state or hot-state Merkle summary logic to exist; the comparator gap itself is not independently triggerable by an unprivileged actor — it is a missing verification layer, not an active state-corruption source. I could not fully trace whether `execution/executor/src/chunk_executor/mod.rs`'s call site independently re-validates checkpoint hashes through `do_state_checkpoint`'s `known_state_checkpoints` mechanism before invoking `ensure_match_transaction_info` (file read returned empty content in this session, so this could not be confirmed). This uncertainty materially affects whether the gap is exploitable in the live chunk-executor commit path or purely confined to offline debugging tools.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present) against locally recomputed values, removing the fail-open TODO, and add a regression test that a mismatched checkpoint hash is rejected by every caller of this function (`chunk_executor`, `replay_on_archive`, `aptos-debugger`, `cli`).

### Proof of Concept
Not independently reproducible from the available index: this is a code-inspection finding based on the explicit TODO and the enumerated field list in `ensure_match_transaction_info` vs. `TransactionInfoV1`'s fields. I was unable to retrieve the content of `execution/executor/src/chunk_executor/mod.rs` in this session (tool returned empty), so I cannot confirm whether the live chunk-executor commit path independently re-validates the checkpoint hashes elsewhere before calling this comparator, which is necessary to establish whether this is purely a replay-verify-tooling blind spot (lower severity) or also affects a live commit/sync path (higher severity). This should be verified with full repository access before treating this as a confirmed high-severity issue.

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

**File:** types/src/transaction/mod.rs (L2440-2461)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct TransactionInfoV1 {
    gas_used: u64,
    status: ExecutionStatus,
    transaction_hash: HashValue,
    event_root_hash: HashValue,
    state_change_hash: HashValue,
    state_checkpoint_hash: Option<HashValue>,
    hot_state_checkpoint_hash: Option<HashValue>,
    auxiliary_info_hash: Option<HashValue>,

    /// Repurposed reserved field; `None` matches the prior BCS encoding.
    position_state_checkpoint_hash: Option<HashValue>,
    placeholder1: Option<HashValue>,
    placeholder2: Option<HashValue>,
    placeholder3: Option<HashValue>,
    placeholder4: Option<HashValue>,
    placeholder5: Option<HashValue>,
    placeholder6: Option<HashValue>,
    placeholder7: Option<HashValue>,
}
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L206-221)
```rust
        if let Some(known) = known_state_checkpoints {
            ensure!(
                known.len() == num_txns,
                "Bad number of known {label} hashes. {} vs {}",
                known.len(),
                num_txns,
            );
            if let Some(idx) = last_checkpoint_index {
                ensure!(
                    known[idx] == Some(computed_last_checkpoint_hash),
                    "{label} root hash mismatch with known hashes passed in. {:?} vs {:?}",
                    known[idx],
                    Some(computed_last_checkpoint_hash),
                );
            }
            Ok(known)
```
