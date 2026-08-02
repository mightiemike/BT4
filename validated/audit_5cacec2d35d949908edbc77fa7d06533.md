### Title
Replay-verify comparator skips state-checkpoint hash validation, masking state-root divergence - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the comparator used by Aptos's off-chain replay/debug tooling to confirm that a locally re-executed transaction matches the authenticated `TransactionInfo` recorded on-chain. The comparator checks status, gas, write-set hash, and event-root hash, but intentionally skips the `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` fields — the very fields that authenticate the Merkle state root. As a result, `replay-verify` (the tool whose job is to catch state-root divergences before they reach mainnet or get hard-forked) can report a "successful" replay even when the locally computed state root differs from the authenticated on-chain root.

### Finding Description
`ensure_match_transaction_info` is defined on `TransactionOutput` and validates a replayed output against an expected `TransactionInfo`: [1](#0-0) 

It checks `status`, `gas_used`, the write-set hash against `state_change_hash`, and the event-root hash — but its own comment admits the gap: [2](#0-1) 
> "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This comparator is exactly what `storage/db-tool/src/replay_on_archive.rs` uses to gate pass/fail of a chunk replay: [3](#0-2) 

It is also used by `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs` (grep confirms one call site each), i.e. it is the standard mechanism operators/CI use to detect execution divergence from archived history.

By contrast, the actual consensus/state-sync commit-time verifier (`ChunkResultVerifier` in `execution/executor/src/chunk_executor/chunk_result_verifier.rs`) uses `ledger_update_output.ensure_transaction_infos_match`, which compares fully assembled `TransactionInfo` objects (including checkpoint hashes) via their accumulator hashes: [4](#0-3) [5](#0-4) 

So the node's live commit path is not affected, but the audit/verification tooling that exists specifically to catch state-root divergence before it becomes a hard-fork incident has a blind spot for exactly that class of bug (e.g. an accidental state-checkpoint/hot-state-root miscomputation introduced during a future feature like `HOT_STATE_ROOT_IN_TXN_INFO`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, referenced in `do_ledger_update.rs`): [6](#0-5) 

### Impact Explanation
This falls under the in-scope "Hard-fork-only divergence during commit, replay, restore, or proof verification" category: `replay-verify` is the tool relied upon (per `testsuite/replay-verify/`) to catch state-computation bugs against historical mainnet data before they cause a network split. Because the checkpoint-hash fields are excluded from the comparison, a bug that corrupts state-checkpoint/hot-state/position-state root computation (independent of write-set/event hashing) would go undetected by this tool, silently passing replay-verify jobs and allowing a latent state-divergence bug to reach production undetected.

### Likelihood Explanation
This is not remotely triggerable by an attacker; the gap only matters when a *different*, currently-hypothetical bug exists in checkpoint-hash computation. The gap itself is confirmed to exist today (self-documented via the TODO comment and the visible field list in `ensure_match_transaction_info`), and is actively used by production replay-verify workflows, but there is no evidence in this repository of an active checkpoint-hash-computation bug being masked right now — I could not find or verify such a co-occurring computation bug, so likelihood of concrete current-day impact is low/unproven; the finding is a genuine, real gap in a proof/state-integrity-relevant safety net rather than a demonstrated wrong-state-commit today.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the expected `TransactionInfo` whenever those fields are `Some` on either side (handling the V0/V1 and feature-gated `None` cases), so `replay-verify`, `aptos-debugger`, and `cli` diverge-detection paths are as strict as the live `ensure_transaction_infos_match` path used in commit-time chunk verification.

### Proof of Concept
Not applicable as an attacker-triggerable exploit — this is a code-review-verified gap in an internal integrity-checking tool, not a network-exploitable vulnerability. Concrete evidence: the TODO/comment in `types/src/transaction/mod.rs` lines 2197-2203 explicitly states the checkpoint hashes are excluded from the check, and `storage/db-tool/src/replay_on_archive.rs` lines 392-397 shows this exact (weaker) function gating replay-verify pass/fail, in contrast to the stricter `ensure_transaction_infos_match` used by the live chunk executor.

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

**File:** execution/executor/src/chunk_executor/chunk_result_verifier.rs (L36-66)
```rust
impl ChunkResultVerifier for StateSyncChunkVerifier {
    fn verify_chunk_result(
        &self,
        parent_accumulator: &InMemoryTransactionAccumulator,
        ledger_update_output: &LedgerUpdateOutput,
    ) -> Result<()> {
        // In consensus-only mode, we cannot verify the proof against the executed output,
        // because the proof returned by the remote peer is an empty one.
        if cfg!(feature = "consensus-only-perf-test") {
            return Ok(());
        }

        THREAD_MANAGER.get_exe_cpu_pool().install(|| {
            let first_version = parent_accumulator.num_leaves();

            // Verify the chunk extends the parent accumulator.
            let parent_root_hash = parent_accumulator.root_hash();
            let num_overlap = self.txn_infos_with_proof.verify_extends_ledger(
                first_version,
                parent_root_hash,
                Some(first_version),
            )?;
            assert_eq!(num_overlap, 0, "overlapped chunks");

            // Verify transaction infos match
            ledger_update_output
                .ensure_transaction_infos_match(&self.txn_infos_with_proof.transaction_infos)?;

            Ok(())
        })
    }
```

**File:** execution/executor/src/chunk_executor/chunk_result_verifier.rs (L129-141)
```rust
pub struct ReplayChunkVerifier {
    pub transaction_infos: Vec<TransactionInfo>,
}

impl ChunkResultVerifier for ReplayChunkVerifier {
    fn verify_chunk_result(
        &self,
        _parent_accumulator: &InMemoryTransactionAccumulator,
        ledger_update_output: &LedgerUpdateOutput,
    ) -> Result<()> {
        ledger_update_output.ensure_transaction_infos_match(&self.transaction_infos)
    }

```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L30-45)
```rust
        // Assemble `TransactionInfo`s. The variant (V0 vs V1) is driven by the
        // `TRANSACTION_INFO_V1` on-chain feature, threaded via
        // `ExecutionOutput::transaction_info_v1`. The hot state root hash a V1 carries is
        // present only when `HOT_STATE_ROOT_IN_TXN_INFO` is also on (`DoStateCheckpoint`
        // produces `Some` hashes iff so); otherwise the V1 leaves it `None`.
        let (transaction_infos, transaction_info_hashes) = Self::assemble_transaction_infos(
            &execution_output.to_commit,
            execution_output.transaction_info_v1,
            &state_checkpoint_output.state_checkpoint_hashes,
            state_checkpoint_output
                .hot_state_checkpoint_hashes
                .as_deref(),
            state_checkpoint_output
                .position_state_checkpoint_hashes
                .as_deref(),
        );
```
