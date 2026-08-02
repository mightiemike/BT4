### Title
`TransactionOutput::ensure_match_transaction_info` never validates the state-checkpoint (SMT root) hashes, letting replay/verify tooling accept a diverged state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function that binds a freshly-computed `TransactionOutput` back to an authenticated `TransactionInfo` (the accumulator leaf that is signed transitively via the `LedgerInfo`). It checks status, gas used, the write-set hash (`state_change_hash`), and the event root hash, but it never compares the locally computed state root to `txn_info.state_checkpoint_hash()`, nor to `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()` on `TransactionInfoV1`. This is the exact same bug-class as the `SpiceAuction.startAuction` report: a value that should be derived/verified from an already-authoritative source (`startTime`/the checkpoint root) is instead silently computed independently (`block.timestamp`/skipped), so the wrong value can be accepted without detection.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` in `types/src/transaction/mod.rs` (~line 2139) performs these checks: [1](#0-0) 

- `status` vs expected status — checked
- `gas_used` vs `txn_info.gas_used()` — checked
- `write_set_hash` vs `txn_info.state_change_hash()` — checked
- `event_root_hash` vs `txn_info.event_root_hash()` — checked

It does **not** compare the actually-computed world-state Merkle root for this version to `txn_info.state_checkpoint_hash()` (nor the analogous `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` fields carried on `TransactionInfoV1`, introduced alongside `DoStateCheckpoint`/`DoLedgerUpdate`). The code itself contains a developer-acknowledged TODO documenting exactly this gap: [2](#0-1) 

This function is consumed directly by the archive replay-verification tool, which trusts its `Ok(())` result as proof that a locally re-executed transaction matches the backed-up, ledger-info-authenticated `TransactionInfo`: [3](#0-2) 

The state-checkpoint hash is precisely the field produced by `DoStateCheckpoint::run` / `DoLedgerUpdate::assemble_transaction_infos`, i.e. the Jellyfish-Merkle/SMT root binding the durable world state to the version: [4](#0-3) [5](#0-4) 

Because `ensure_match_transaction_info` never re-derives or checks this root against the expected value, a replay path can diverge in state (e.g., due to a VM/storage bug, a bad backup entry, or an execution non-determinism) purely on the state tree while still reporting a "successful" verification, since only the write-set hash (input side) and event hash are checked — not the resulting committed state root (output side).

### Impact Explanation
This breaks the "authenticated proof-bearing responses/replay paths must stay bound to the right ledger version and root" invariant called out in the task's scope. Concretely: `db-tool replay-verify` (and any other caller relying on `ensure_match_transaction_info` as its correctness oracle for restore/replay) can certify that replayed execution matches the archived, signature-authenticated ledger data even when the actual Sparse Merkle Tree / hot-state root / position-state root diverges from the correct value. This is a state-commitment/proof-integrity gap: it can mask silent state corruption during restore/replay verification, which is one of Aptos's primary mechanisms for catching non-determinism or storage bugs before they propagate. It does not directly forge a signed `LedgerInfo`/accumulator proof on the live consensus path (validators separately authenticate `state_checkpoint_hash` via the signed accumulator), so it is not a full consensus-fork primitive by itself, but it defeats the intended detection mechanism for exactly that class of divergence in the offline/replay tooling that operators depend on to certify DB integrity.

### Likelihood Explanation
No privileged access or malicious actor is required — the gap is unconditionally present in the comparator function every time it's invoked (both in `db-tool replay_on_archive` and any other verification caller). Any state-root-level execution/storage divergence (bug, non-determinism, or corrupted backup data) that leaves the write-set hash and event hash unchanged, or where the tool simply doesn't inspect the omitted fields, will pass silently. This is a straightforward, always-reachable logic omission rather than a narrow edge case.

### Recommendation
Extend `ensure_match_transaction_info` to recompute the current state root (from whatever state-checkpoint output is available to the caller) and assert it equals `txn_info.state_checkpoint_hash()` when a checkpoint is expected at that version, and analogously validate `hot_state_checkpoint_hash()` and `position_state_checkpoint_hash()` for `TransactionInfoV1` when those features are enabled — mirroring the write_set_hash/event_root_hash pattern already present. At minimum, `replay_on_archive.rs` should be updated to independently verify the checkpoint hashes it obtains from the backup against the freshly computed state view before declaring a chunk verified.

### Proof of Concept
Conceptual (no PoC harness available in the index): construct a `TransactionOutput` whose `write_set` and `events` hash-match an expected `TransactionInfo`, but whose actual committed state (as it would be checkpointed by `DoStateCheckpoint`) differs from `txn_info.state_checkpoint_hash()`. Call `ensure_match_transaction_info` (as `replay_on_archive::execute_and_verify` does) — it returns `Ok(())` despite the state-root mismatch, since `state_checkpoint_hash` is never inspected, as shown at [6](#0-5) .

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L82-121)
```rust
                let state_checkpoint_hash = state_checkpoint_hashes[i];
                let event_hashes = txn_output
                    .events()
                    .iter()
                    .map(CryptoHash::hash)
                    .collect::<Vec<_>>();
                let event_root_hash =
                    InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash();
                let write_set_hash = CryptoHash::hash(txn_output.write_set());
                let status = txn_output
                    .status()
                    .as_kept_status()
                    .expect("Already sorted.");
                let txn_info = if transaction_info_v1 {
                    TransactionInfo::builder_v1()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .maybe_hot_state_checkpoint_hash(
                            hot_state_checkpoint_hashes.and_then(|hot| hot[i]),
                        )
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .maybe_position_state_checkpoint_hash(
                            position_state_checkpoint_hashes.and_then(|p| p[i]),
                        )
                        .build()
                } else {
                    TransactionInfo::builder_v0()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .build()
                };
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L44-49)
```rust
        let state_checkpoint_hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_state_checkpoints,
            last_checkpoint.root_hash(),
            "state",
        )?;
```
