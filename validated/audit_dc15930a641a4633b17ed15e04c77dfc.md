### Title
`ensure_match_transaction_info` skips checkpoint-hash comparison, letting replay-verify tooling accept a divergent state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` — the routine used by both the chunk executor's `verify_execution` path and `db-tool replay-on-archive` to confirm that freshly-executed transaction outputs match previously-committed `TransactionInfo` records — checks status, gas used, write-set hash, and event root hash, but never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. The code even contains a TODO acknowledging this gap.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` [1](#0-0)  validates a re-executed `TransactionOutput` against a stored `TransactionInfo` by checking:
- execution status
- gas used
- `write_set_hash == txn_info.state_change_hash()`
- `event_root_hash == txn_info.event_root_hash()`

but it explicitly does **not** validate the state-checkpoint-derived hashes carried in `TransactionInfo` (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`), as documented in the trailing comment: "this comparator ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution." [2](#0-1) 

This function is the sole correctness gate in two integrity-critical call sites:
1. `ChunkExecutor::verify_execution`, which re-executes a chunk against local state and calls `txn_out.ensure_match_transaction_info(version, txn_info, Some(write_set), Some(events))` to decide whether the chunk's committed data is trustworthy. [3](#0-2) 
2. `db-tool`'s `replay_on_archive::Verifier::execute_and_verify`, which re-executes historical transactions and calls the same `ensure_match_transaction_info` to certify that an archived DB's ledger matches independent re-execution. [4](#0-3) 

Meanwhile, the state-checkpoint hash is a first-class, authenticated field of `TransactionInfo`, computed from the JMT/state-checkpoint root during normal execution in `DoLedgerUpdate::assemble_transaction_infos` [5](#0-4)  and it is this hash (via the transaction-info leaf hash) that is bound into the transaction accumulator that ledger-info signatures ultimately commit to. Since `ensure_match_transaction_info` never recomputes/compares it, if the archived write set/status/gas/events happen to match but the recorded `state_checkpoint_hash` (or hot/position state checkpoint hash) is wrong or was produced by a different state root, replay-verify and chunk verification will both report success.

### Impact Explanation
This breaks the "wrong accumulator root / state proof accepted as valid" invariant for a subset of the total commitment: the leaf hash of `TransactionInfo` in the transaction accumulator incorporates the state-checkpoint hash, yet the tooling meant to catch a divergence between an archived/synced ledger and honest local re-execution does not check that hash. On mainnet this means:
- `db-tool replay-on-archive`, used to independently audit archived ledger data, can certify an archive as "correctly replayed" even if the recorded state root diverges from the actual VM-computed state (e.g., due to a storage bug, corrupted backup, or non-deterministic state-checkpoint computation) as long as write set, events, gas, and status happen to match.
- `ChunkExecutor::verify_execution`, used in fast-sync/backup-restore verification flows, has the same blind spot for the state-checkpoint/hot-state/position-state roots that are unique to `TransactionInfoV1`.

This is a genuine gap in state-commitment auditing tooling rather than a live consensus-safety break (a normal, honestly-computed state-checkpoint hash still gets bound into the accumulator and verified cryptographically elsewhere when a `LedgerInfo`/accumulator proof is checked against a validator-signed root). Its practical severity is bounded by the fact that it only matters when the write set/events/gas/status match but the state-checkpoint hash differs — a scenario that should be extremely rare in correctly functioning code, but the comment shows the authors are aware it is currently unguarded, which is precisely the kind of "should verify but doesn't" integrity gap requested by the report class.

### Likelihood Explanation
Low-to-moderate likelihood of being triggered by an actual bug elsewhere (e.g. a state-checkpoint/hot-state computation regression that still produces the same write set/events by construction), but the code's own comment confirms the authors recognize this as a real, currently-unmitigated gap in a security-relevant verification tool, and it requires no privileged access to exploit — it is a silent false-positive in defense-in-depth tooling used precisely to catch such divergences.

### Recommendation
Extend `ensure_match_transaction_info` to recompute the state-checkpoint hash (and hot-state/position-state checkpoint hashes when `TransactionInfoV1` is used) from the post-execution state view and compare it against `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`, as flagged by the existing TODO before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`-dependent flows in `replay_on_archive` and chunk-executor verification.

### Proof of Concept
Not independently reproducible as a live consensus exploit from this code path alone — the finding is a self-acknowledged verification gap (see the TODO in `types/src/transaction/mod.rs` lines 2197-2203) rather than a demonstrated corrupted mainnet state; confirming an end-to-end PoC would require constructing a state-checkpoint-hash divergence (e.g., a bug in hot-state or position-state checkpoint computation) that leaves write set, events, gas, and status untouched, which requires further code exploration beyond what the available tool budget allowed. [1](#0-0)

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

**File:** execution/executor/src/chunk_executor/mod.rs (L685-706)
```rust
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
