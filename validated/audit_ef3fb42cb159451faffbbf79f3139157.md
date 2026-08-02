I'll investigate the areas mentioned—write-set conversion, transaction info construction, storage commit, accumulators, and restore flows—to see if there's a genuine, locally-provable integrity issue analogous to the reported bug pattern (a mismatch between an assumed/authorized value and an actual value causing incorrect behavior).## Finding

The bug-report's core invariant is: "a validation function must check the *actual* protected quantity, not skip it silently, or the safety check becomes a no-op for the common case." The Aptos-native analog I found is in the transaction-output/replay verification code: `TransactionOutput::ensure_match_transaction_info()` checks status, gas, write-set hash, and event-root hash, but explicitly and admittedly skips checking `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` — the actual state (Merkle) roots — even though this function is the sole correctness gate used by the mainnet-archive replay-verification tooling.

### Title
Replay-verification skips state-root comparison, masking state divergence - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function `storage/db-tool/src/replay_on_archive.rs` (and `aptos-move/cli`, `aptos-move/aptos-debugger`) use to confirm that locally re-executing archived mainnet transactions reproduces the same result as the historically recorded `TransactionInfo`. The function never compares the state-checkpoint hash (the Sparse-Merkle-Tree/JMT root committed on-chain), so a locally computed state root that diverges from the authenticated on-chain root is never detected.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  validates status, gas used, write-set hash (`state_change_hash`), and event root hash against the supplied `TransactionInfo`, but the function body contains an explicit acknowledgement that the state/hot-state/position-state checkpoint hashes are not compared: [2](#0-1) 

This function is invoked by `storage/db-tool/src/replay_on_archive.rs::execute_and_verify`, which re-executes archived transactions with `AptosVMBlockExecutor` and treats a successful call to `ensure_match_transaction_info` as proof that the replay matches the archived (previously-committed, ledger-info-signed) result: [3](#0-2) 

Because the state checkpoint hash is never checked here, a divergence in the *state* produced by re-execution (as opposed to the write-set of a single transaction, which is checked) can pass this tool's verification silently. This is exactly the class of bug that `replay_on_archive`/`replay_verify` exists to catch: nondeterministic or newly-introduced VM/state-commit behavior that would cause a chain split (hard fork) if it ever occurred on live consensus nodes, since different validators would compute different state roots for the same transactions.

Contrast this with the actual consensus-critical path, which is safe: `execution/executor/src/chunk_executor/chunk_result_verifier.rs`'s `ReplayChunkVerifier` calls `ledger_update_output.ensure_transaction_infos_match`, a different function that compares full, reconstructed `TransactionInfo` objects (including checkpoint hashes) — so state sync / chunk-execution replay is not affected. The gap is isolated to the archive-based, offline `db-tool replay-on-archive` / `aptos-debugger` code paths, which are the tools operators and Aptos Labs run over historical mainnet data to detect exactly this class of divergence.

### Impact Explanation
This does not corrupt live consensus state directly (the accumulator/ledger-info signature check remains intact for normal sync), so it does not let an attacker post a bad state root on-chain. However, it defeats the one mechanism designed to catch and prevent it: a state-divergence bug (e.g., an execution-determinism or state-commit bug introduced by a future change) replayed against real mainnet history via `replay_on_archive` would report success even though the locally computed state root differs from the authenticated on-chain root. This falls squarely under the in-scope category "Hard-fork-only divergence during commit, replay, restore, or proof verification" — the tool exists specifically to catch pre-hard-fork state divergence and currently cannot.

### Likelihood Explanation
The condition is deterministic and always present — it is not a race or edge case; the check is unconditionally absent from the code for every replayed transaction that produces a state checkpoint. The trigger requires only that a genuine execution/state-commit divergence exist somewhere else in the codebase (execution nondeterminism, a state-commit bug, a JMT/hot-state computation bug, etc.) — likelihood of that pre-condition is separate, but given that condition, this gap guarantees the tool fails to detect it. The code's own inline TODO comment confirms the Aptos team is aware this is an open gap ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS").

### Recommendation
Extend `ensure_match_transaction_info` to compare the locally computed state-checkpoint hash (and hot-state/position-state checkpoint hashes when applicable) against `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`, mirroring the treatment already given to `state_change_hash` and `event_root_hash`, before this function is relied upon by any replay-verification tooling used to certify mainnet execution correctness.

### Proof of Concept
Not directly exploitable as a state-corruption PoC (this is a missing-check in an offline verification tool, not a consensus-path bug). The "PoC" is structural: any test that supplies a `TransactionOutput` with a correct write-set/events but a state produced by execution whose SMT root differs from the `TransactionInfo.state_checkpoint_hash` passed in will still pass `ensure_match_transaction_info` — demonstrated by the fact that no code path in [1](#0-0)  reads `state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` from `txn_info` at all.

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
