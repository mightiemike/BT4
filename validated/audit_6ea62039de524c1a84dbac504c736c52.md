## Title
`replay-verify` (`ensure_match_transaction_info`) never checks the state-checkpoint root, allowing a state-root divergence to pass verification - (File: `types/src/transaction/mod.rs`)

### Summary
The `TransactionOutput::ensure_match_transaction_info` function is the sole authenticated-invariant check used by the archive replay-verification tool (`storage/db-tool/src/replay_on_archive.rs`) to confirm that locally re-executed transactions produced the exact same committed ledger result as the one recorded and proven in a backup/archive. This function checks execution status, gas used, write-set hash (`state_change_hash`), and event root hash, but it never checks `state_checkpoint_hash` — the Jellyfish Merkle Tree root that is the actual authoritative commitment of the post-transaction world state. This gap is even called out by an unresolved TODO comment directly above the `Ok(())` return.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` is defined in [1](#0-0) . It validates:
- status vs `txn_info.status()`
- `gas_used()` vs `txn_info.gas_used()`
- `CryptoHash::hash(self.write_set())` vs `txn_info.state_change_hash()`
- computed event root vs `txn_info.event_root_hash()`

It explicitly does **not** compare `txn_info.state_checkpoint_hash()` (nor `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against anything computed from local re-execution, as documented in the code itself: [2](#0-1) 

`state_change_hash` is only the hash of the transaction's own write set — not of the resulting global state tree — as documented on `TransactionInfoV0`: [3](#0-2) 

`state_checkpoint_hash` is the actual Sparse/Jellyfish Merkle root of the entire world state after the transaction (materialized periodically, e.g., per block) — this is the value that backup/restore code independently treats as authoritative (e.g. `ensure_state_checkpoint_hash()` compared against a manifest `root_hash` during state-snapshot restore, seen in [4](#0-3) ).

This function is invoked as the pass/fail check for every re-executed transaction chunk by the `replay_on_archive` verifier: [5](#0-4) 

Because `ensure_match_transaction_info` never checks the state-checkpoint hash, `replay_on_archive` can declare a chunk of an archive "verified" even though the actual committed state root diverges from the archived/proven `TransactionInfo.state_checkpoint_hash`. Any divergence that does not manifest as a different write-set hash for that individual transaction (e.g., corruption introduced during storage commit, a nondeterministic state-application bug in JMT construction/aggregation across transactions, hot-state materialization bugs, or a tampered archive whose per-txn write-sets are unmodified but whose checkpoint hash was altered) will not be detected by this tool.

### Impact Explanation
`replay_on_archive` is the integrity tool operators use to confirm an archived/backed-up ledger segment is consistent with independent re-execution — i.e., the practical mechanism for detecting state-commitment corruption or non-determinism bugs after the fact. Silently missing the state-checkpoint-hash check means this tool provides false assurance: a divergence in the authoritative Merkle state root (the actual "world state" ledger commitment) can exist in an archive and pass verification undetected. This is exactly the "authenticated ... state-view output bound to the wrong version/root" and "hard-fork-only divergence during ... replay" class of impact — a real bug that corrupts durable state (e.g. from a rare nondeterministic execution/materialization defect) could go unnoticed by the very tool meant to catch it, delaying detection of a chain split or corrupted archive used for fast-sync/restore.

### Likelihood Explanation
The gap is deterministic and always present — it's not conditioned on an attacker action, but on the existence of a divergence whose per-transaction write set still hashes identically (a real, if rare, precondition, e.g., bugs affecting cross-transaction JMT construction, hot-state checkpointing, or archive tampering that only touches the state-checkpoint field). The comment in the source confirms the aptos engineers are aware this window exists (`COMPUTE_TRADING_NATIVE_STATE_ROOTS` is referenced as the planned mitigation but not yet wired into this comparator).

### Recommendation
In `ensure_match_transaction_info`, when `txn_info.state_checkpoint_hash()` is `Some(..)` (i.e., at a checkpoint boundary), compute the resulting state root from local re-execution (via `TStateView`/JMT root at that version) and assert it equals the archived value, mirroring the pattern already used in `storage/backup/backup-cli/.../state_snapshot/restore.rs`. Do the same for `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

### Proof of Concept
1. Construct (or simulate) an archived transaction chunk where the per-transaction `WriteSet` bytes for a given txn are unchanged (so `state_change_hash` matches) but the transaction is a checkpoint transaction whose `state_checkpoint_hash` field in the stored `TransactionInfo` has been altered/corrupted (or diverges due to an accumulation bug elsewhere in the pipeline).
2. Run `replay_on_archive::Verifier::verify` (via `execute_and_verify` at [6](#0-5) ) against this archive.
3. Observe that `ensure_match_transaction_info` returns `Ok(())` for that transaction (status, gas, write-set hash, and event hash all match), and the tool reports the chunk as successfully verified — despite the state-checkpoint root divergence, which none of the checks in [1](#0-0)  ever examine.

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

**File:** types/src/transaction/mod.rs (L2405-2412)
```rust
    /// The hash value summarizing all changes caused to the world state by this transaction.
    /// i.e. hash of the output write set.
    state_change_hash: HashValue,

    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,
```

**File:** storage/backup/backup-cli/src/backup_types/state_snapshot/restore.rs (L125-136)
```rust
        let (txn_info_with_proof, li): (TransactionInfoWithProof, LedgerInfoWithSignatures) =
            self.storage.load_bcs_file(&manifest.proof).await?;
        txn_info_with_proof.verify(li.ledger_info(), manifest.version)?;
        let state_root_hash = txn_info_with_proof
            .transaction_info()
            .ensure_state_checkpoint_hash()?;
        ensure!(
            state_root_hash == manifest.root_hash,
            "Root hash mismatch with that in proof. root hash: {}, expected: {}",
            manifest.root_hash,
            state_root_hash,
        );
```

**File:** storage/db-tool/src/replay_on_archive.rs (L373-406)
```rust
        let executed_outputs = executor
            .execute_block(
                &txns_provider,
                &self
                    .arc_db
                    .state_view_at_version(current_version.checked_sub(1))?,
                BlockExecutorConfigFromOnchain::new_no_block_limit(), // TODO(HotState): will need to incorporate some features.
                TransactionSliceMetadata::Chunk {
                    begin: *current_version,
                    end: *current_version + cur_txns.len() as u64,
                },
            )
            .map(BlockOutput::into_transaction_outputs_forced)?;
        assert_eq!(executed_outputs.len(), cur_txns.len());

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
