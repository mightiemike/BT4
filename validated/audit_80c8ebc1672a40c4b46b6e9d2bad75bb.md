### Title
`ensure_match_transaction_info` skips state/hot-state/position checkpoint-hash comparison, letting replay-verify accept a diverged state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole invariant check binding a freshly re-executed `TransactionOutput` to the authenticated, previously-committed `TransactionInfo` during replay/verify flows. It validates status, gas, write-set hash, and event-root hash, but explicitly (per its own TODO comment) does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the three fields that actually commit to the resulting world-state Merkle root. This means `storage/db-tool/src/replay_on_archive.rs`, which uses this function as its pass/fail criterion, can report a chunk as successfully verified even when the locally recomputed state root diverges from the authenticated on-chain root.

### Finding Description
`TransactionInfo` (both `V0` and `V1`) carries `state_checkpoint_hash`, and `V1` additionally carries `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`: [1](#0-0) [2](#0-1) 

These fields are part of `TransactionInfo`, which is hashed and used as an accumulator leaf that is Merkle-proven against the ledger info (`TransactionInfoListWithProof::verify` / `AccumulatorRangeProof::verify`), i.e. they are authenticated, proof-bound state-root commitments: [3](#0-2) 

`ensure_match_transaction_info` is documented as the function used to validate that a re-executed `TransactionOutput` matches the archived/authenticated `TransactionInfo`. It checks status, gas, `state_change_hash` (write-set hash), and `event_root_hash`, but its own inline comment admits the checkpoint hashes are skipped: [4](#0-3) 

This function is the single verification gate in the `replay-verify` tool (`storage/db-tool/src/replay_on_archive.rs`), which re-executes archived transactions and compares outputs against the archived `expected_txn_infos` fetched from backup storage: [5](#0-4) 

Because `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` are never recomputed from the local Jellyfish Merkle tree state and compared to the archived, ledger-info-authenticated values, a scenario where the write set for a given transaction hashes identically but the *resulting state root* diverges (e.g., due to a state-application bug in the state-checkpoint/hot-state path, prior undetected cumulative drift, or hard-fork-only divergence in how state is merged into the SMT) will not be caught. The tool will report "successful replay" while the authenticated state root has, in fact, diverged.

### Impact Explanation
This breaks the "authenticated API / proof-bearing responses must stay bound to the right ledger version, root, and object" and "restore/replay paths must not reinterpret committed data into a different ledger state" invariants explicitly called out in scope. Replay-verify is the tool operators and auditors rely on to detect hard forks or state corruption between local execution and the canonical, signed ledger history. A silent gap here means state-root divergence (the exact class of bug a hard fork produces) can pass verification undetected, since only the write-set and event hashes are checked, not the actual committed Merkle root fields carried in `TransactionInfo`. This is a state-commitment/proof-integrity impact matching "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Wrong accumulator root ... proof ... accepted as valid" in the state-integrity gate.

### Likelihood Explanation
The gap is not theoretical — it is explicitly acknowledged by the maintainers in the surrounding TODO comment, meaning it's a known, currently-shipped incompleteness in a security-relevant verification tool. Any state-computation bug or hard-fork condition that changes the checkpoint hash while preserving per-transaction status/gas/write-set/event equality (a plausible situation, since the checkpoint hash also depends on the previously accumulated state root, not solely the current write set) will go undetected by `replay_on_archive`. This is gated behind the not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature, which limits current exposure to whenever that feature path is exercised or once it is enabled — but the check is missing today at the shared library level (`types/src/transaction/mod.rs`), not only within a feature-flagged branch, so any current or future caller of `ensure_match_transaction_info` (including `aptos-move/aptos-debugger/src/aptos_debugger.rs`) inherits the same gap.

### Recommendation
Extend `ensure_match_transaction_info` to recompute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against `txn_info`'s corresponding fields whenever those hashes are available/expected for the given transaction (i.e., checkpoint transactions), matching the write-set/event-root comparison pattern already present. This should be resolved before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, as its own comment states, and ideally regardless of that flag so `replay_on_archive` and other consumers get complete verification today.

### Proof of Concept
Not independently reproducible as a runtime exploit from this static analysis (no test harness access here), but the code path is directly demonstrable by inspection:
1. `types/src/transaction/mod.rs:2139-2204` — `ensure_match_transaction_info` checks `status`, `gas_used`, `state_change_hash`, `event_root_hash` only; the trailing comment (lines 2197-2202) confirms `state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash` are intentionally skipped.
2. `storage/db-tool/src/replay_on_archive.rs:388-406` — `execute_and_verify` calls this function as the only success/failure criterion per transaction during replay-verify against archived data.
3. `types/src/transaction/mod.rs:2440-2461` — `TransactionInfoV1` defines the unchecked fields, and `types/src/proof/definition.rs:908-925` confirms these fields are part of the accumulator-proven, ledger-info-authenticated `TransactionInfo` hash — i.e., real proof material that the replay tool should be, but currently is not, validating.

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

**File:** types/src/transaction/mod.rs (L2409-2416)
```rust
    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,

    /// The hash value summarizing PersistedAuxiliaryInfo.
    auxiliary_info_hash: Option<HashValue>,
}
```

**File:** types/src/transaction/mod.rs (L2448-2453)
```rust
    state_checkpoint_hash: Option<HashValue>,
    hot_state_checkpoint_hash: Option<HashValue>,
    auxiliary_info_hash: Option<HashValue>,

    /// Repurposed reserved field; `None` matches the prior BCS encoding.
    position_state_checkpoint_hash: Option<HashValue>,
```

**File:** types/src/proof/definition.rs (L908-925)
```rust
    /// Verifies the list of transaction infos are correct using the proof. The verifier
    /// needs to have the ledger info and the version of the first transaction in possession.
    pub fn verify(
        &self,
        ledger_info: &LedgerInfo,
        first_transaction_info_version: Option<Version>,
    ) -> Result<()> {
        let txn_info_hashes: Vec<_> = self
            .transaction_infos
            .iter()
            .map(CryptoHash::hash)
            .collect();
        self.ledger_info_to_transaction_infos_proof.verify(
            ledger_info.transaction_accumulator_hash(),
            first_transaction_info_version,
            &txn_info_hashes,
        )
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
