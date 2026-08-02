### Title
`ensure_match_transaction_info` never verifies `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, letting replay-verify tooling accept a corrupted state root - (File: `types/src/transaction/mod.rs`)

### Summary
The comparator `TransactionOutput::ensure_match_transaction_info`, which is the authoritative check used by replay-verify and debugger tooling to confirm a locally re-executed transaction matches the archived/committed `TransactionInfo`, only validates status, gas used, write-set hash (`state_change_hash`), and event root hash. It explicitly skips comparing the `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` fields carried in `TransactionInfo`, as acknowledged by its own TODO comment.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  checks four properties between a freshly-computed `TransactionOutput` and an archived `TransactionInfo`: execution status, gas used, write-set hash equality with `state_change_hash`, and event root hash. It ends with: [2](#0-1) 

This comment admits the function "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`)". Critically, this is not gated behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS` — the omission applies to **all** three checkpoint hash fields, including the plain `state_checkpoint_hash`, which is the periodically-emitted Sparse-Merkle/Jellyfish-Merkle state root committed on-chain via `TransactionInfoV0`/`V1` [3](#0-2) .

This function is the sole correctness gate used by:
- `storage/db-tool/src/replay_on_archive.rs`, the tool operators run to detect execution/state divergence against mainnet archives: [4](#0-3) 
- The CLI transaction replay command: [5](#0-4) 
- `aptos-debugger`'s mismatch reporting: [6](#0-5) 

The state root itself is computed separately from the write set, in `DoStateCheckpoint`/buffered-state code paths (JMT/SMT construction, hot-state root, and — when enabled — the "trading-native" position-state root referenced in `execution/executor/src/workflow/do_state_checkpoint.rs` and `do_ledger_update.rs`). A bug in that checkpoint/root-construction logic (e.g., an incorrect JMT update, a stale buffered-state base, or incorrect hot/position root merge) can produce a `state_checkpoint_hash` that diverges from the true chain state while the per-transaction write set is still bit-for-bit correct. Because `ensure_match_transaction_info` never compares this field, such a divergence is silently accepted as a "match" by every consumer of this function.

### Impact Explanation
This breaks the proof/commitment integrity invariant required by the State-Integrity Gate: "Wrong accumulator root ... Merkle proof ... accepted as valid" and "Hard-fork-only divergence during commit, replay, restore, or proof verification." Replay-verify against mainnet archives is the primary tool for catching exactly this class of bug (a state-root/JMT computation defect) before or after a network upgrade. Because the check is missing, a corrupted `state_checkpoint_hash` — which underlies every state proof served to light clients and the API — could pass replay-verify undetected, delaying or preventing detection of ledger state corruption or a consensus-breaking bug. This is a high-severity integrity gap even though it does not by itself write bad state (it is a missing-detection issue, not a false-acceptance-by-a-validator issue), because it removes the safety net specifically designed to catch state-root corruption bugs.

### Likelihood Explanation
The gap is unconditional (present in the code today, not hidden behind an unshipped feature flag for the base `state_checkpoint_hash` field) and is hit on every replay-verify/debugger run, since `ensure_match_transaction_info` is called for every transaction and never exercises the checkpoint-hash branch. It requires no attacker action to trigger the blind spot — only an actual bug in state-checkpoint computation (which is precisely the failure mode replay-verify is meant to catch) would be missed. Likelihood of the underlying state-root bug occurring is separate from this issue; the issue here is that if it does occur, this tooling will not catch it.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash` (and, when available, `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) between the locally computed checkpoint output and the archived `TransactionInfo`, following the guidance already present in the TODO comment, before relying on `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or shipping replay-verify results as an integrity guarantee.

### Proof of Concept
1. Assume a bug exists in `DoStateCheckpoint`'s SMT/JMT root computation (e.g., in `execution/executor/src/workflow/do_state_checkpoint.rs`) that produces an incorrect `state_checkpoint_hash` for a given checkpoint version while all individual write sets remain correct.
2. Run `replay_on_archive` (or the CLI/`aptos-debugger` replay commands) over the affected version range.
3. `execute_and_verify` calls `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_infos[idx], ...)` [7](#0-6) .
4. Because `ensure_match_transaction_info` never inspects `state_checkpoint_hash`, the call returns `Ok(())` despite the archived and locally-computed state roots diverging, and the tool reports a clean replay.

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

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L233-246)
```rust
    fn print_mismatches(
        txn_outputs: &[TransactionOutput],
        expected_txn_infos: &[TransactionInfo],
        first_version: Version,
    ) {
        for idx in 0..txn_outputs.len() {
            let txn_output = &txn_outputs[idx];
            let txn_info = &expected_txn_infos[idx];
            let version = first_version + idx as Version;
            txn_output
                .ensure_match_transaction_info(version, txn_info, None, None)
                .unwrap_or_else(|err| println!("{}", err))
        }
    }
```
