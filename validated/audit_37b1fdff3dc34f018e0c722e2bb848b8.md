Based on the investigation, I found a genuine local integrity gap analogous to the "skip validation on an edge value → downstream corruption goes undetected" bug class from the VUSD report — but here it's a **skipped hash field in the replay-verification comparator**, not a zero-value skip.

### Title
Replay-verification comparator skips state/hot-state/position checkpoint hashes, allowing divergent state roots to pass as "verified" - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single comparator used across all of Aptos's replay-verification tooling (chunk executor replay-verify, `db-tool`'s `replay_on_archive`, `aptos-debugger`, and CLI transaction replay) to confirm that a locally re-executed transaction produced the same result as the one recorded/committed in `TransactionInfo`. It checks status, gas, write-set hash (`state_change_hash`), and event root hash — but explicitly, by its own in-code TODO, skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`.

### Finding Description [1](#0-0) 

The comparator body only validates status, gas used, `state_change_hash` (write-set hash), and event root hash, then explicitly documents the gap: [2](#0-1) 

This function is the sole correctness gate in several independently-invoked replay/verification code paths:
- Chunk executor's execution-verification during replay/state-sync catch-up: [3](#0-2) 
- `db-tool`'s archive replay-verify tool, whose entire job is to detect state divergence before it reaches mainnet: [4](#0-3) 
- `aptos-debugger`'s mismatch reporting: [5](#0-4) 
- CLI transaction replay comparison: [6](#0-5) 

`state_checkpoint_hash` is a real, authenticated field baked into every `TransactionInfo` (and thus into the transaction accumulator / ledger info signature) — it represents the Jellyfish Merkle root of global state at a checkpoint boundary: [7](#0-6)  Yet the one function relied upon by all "did replay produce the correct chain state" tooling never re-derives and compares it.

### Impact Explanation
Because the write-set hash check (`state_change_hash`) only verifies that individual key/value writes match, it does **not** prove the resulting global Merkle tree (state root) is correct — that is precisely what `state_checkpoint_hash` is for. Any bug that corrupts JMT construction, checkpoint boundary selection, or hot-state root computation, while still producing byte-identical per-transaction write sets, will be silently accepted by every one of the affected replay-verify tools as a "successful" match, even though the actual state root recorded on ledger diverges from what local re-execution computes. This directly matches the required "Hard-fork-only divergence during commit, replay, restore, or proof verification" impact class: these tools exist specifically to catch state-root-affecting bugs before they are shipped/relied upon (e.g., validating a new execution engine or VM change against historical archive data, or state-sync catch-up verification), and this gap defeats that safety purpose for the state and hot-state root categories.

### Likelihood Explanation
This is not a hypothetical gap — it is explicitly acknowledged in the code as a known, currently-shipped limitation (`TODO(trading-native): ... Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS`), meaning any latent bug in JMT/hot-state root computation introduced in the interim will not be caught by `replay_on_archive`, chunk-executor replay-verify, or CLI/debugger tooling, regardless of feature-flag state for `state_checkpoint_hash`/`hot_state_checkpoint_hash` (only the `position_state_checkpoint_hash` piece is explicitly gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`).

### Recommendation
Extend `ensure_match_transaction_info` to recompute and compare `state_checkpoint_hash` and `hot_state_checkpoint_hash` (when present in `txn_info`) against the state/hot-state root produced by local re-execution, in addition to the existing write-set/event/gas/status checks, before considering a replayed transaction "matched."

### Proof of Concept
1. Introduce (hypothetically, e.g. via a future patch) a bug that corrupts JMT checkpoint-root computation but leaves individual write ops unchanged (e.g., wrong ordering/hashing when building the state Merkle tree, or an off-by-one on which version is treated as the checkpoint boundary).
2. Run `db-tool replay-on-archive` (or chunk-executor replay-verify during state sync) against historical data containing that checkpoint.
3. `ensure_match_transaction_info` checks `state_change_hash` (write set) — still matches, since raw writes are untouched.
4. It never re-derives `state_checkpoint_hash`, so the tool reports success even though the recomputed global state root differs from the one committed in `TransactionInfo`/accumulator, exactly as flagged in the code's own TODO.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L388-405)
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

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```

**File:** storage/aptosdb/src/db/test_helper.rs (L159-188)
```rust
            let state_checkpoint_root_hash = smt.root_hash();

            // make real txn_info's
            for (idx, txn) in txns_to_commit.iter_mut().enumerate() {
                let placeholder_txn_info = txn.transaction_info();

                // calculate event root hash
                let event_hashes: Vec<_> = txn.events().iter().map(CryptoHash::hash).collect();
                let event_root_hash = InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash();

                // calculate state checkpoint hash and this must be the last txn
                let state_checkpoint_hash = if txn.has_state_checkpoint_hash() {
                    Some(state_checkpoint_root_hash)
                } else {
                    None
                };

                let auxiliary_info = AuxiliaryInfo::new(PersistedAuxiliaryInfo::V1 { transaction_index: idx as u32 }, None);

                let txn_info = TransactionInfo::builder_v0()
                    .transaction_hash(txn.transaction().committed_hash())
                    .state_change_hash(txn.write_set().hash())
                    .event_root_hash(event_root_hash)
                    .maybe_state_checkpoint_hash(state_checkpoint_hash)
                    .gas_used(placeholder_txn_info.gas_used())
                    .status(placeholder_txn_info.status().clone())
                    .maybe_auxiliary_info_hash(auxiliary_info.persisted_info_hash())
                    .build();
                txn_accumulator = txn_accumulator.append(&[txn_info.hash()]);
                txn.set_transaction_info(txn_info);
```
