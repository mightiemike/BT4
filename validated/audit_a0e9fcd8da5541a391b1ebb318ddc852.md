I have enough evidence to write up this finding now.

### Title
`ensure_match_transaction_info` skips checkpoint-hash comparisons, letting replay-verify report success on a diverged state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the comparator used by all replay-verification tooling (`db-tool replay-on-archive`, `verify_execution` in the chunk executor, and `aptos-debugger`) to confirm that locally re-executed transaction outputs match the authenticated `TransactionInfo` recorded in the archived/backed-up ledger (which is itself covered by the transaction accumulator and signed `LedgerInfo`). The function checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly does not check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the very fields that authenticate the resulting state/JMT root at checkpoint boundaries.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  ends with a self-documented gap:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [2](#0-1) 

This comparator is the sole correctness check used by:
- `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which replays archived transactions through `AptosVMBlockExecutor` and calls `executed_outputs[idx].ensure_match_transaction_info(...)` against `expected_txn_infos` pulled straight from the (potentially malicious or corrupted) archive/backup source [3](#0-2) .
- `execution/executor/src/chunk_executor/mod.rs`'s `verify_execution`, used by the `db-tool replay-verify` / `ReplayChunkVerifier` path during backup restore verification [4](#0-3) .
- `aptos-move/aptos-debugger/src/aptos_debugger.rs`'s `print_mismatches`, used to diagnose execution divergence [5](#0-4) .

None of these call sites separately validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the recomputed state summary; they rely entirely on `ensure_match_transaction_info`. By contrast, the online consensus/state-sync commit path in `LedgerUpdateOutput::ensure_transaction_infos_match` performs full `TransactionInfo` struct equality (which does include checkpoint hashes) [6](#0-5) , so this gap is confined to the offline replay-verification/audit tooling rather than the consensus-critical commit path.

### Impact Explanation
Replay-verification against backups/archives is the primary mechanism operators and Aptos Labs use to detect non-determinism bugs, storage corruption, or a maliciously/incorrectly served archive that produces a wrong world-state root (JMT root), while still emitting the correct write set, events, gas, and status for a transaction. Because `ensure_match_transaction_info` omits the checkpoint-hash fields, such a divergence would go completely undetected: `replay-on-archive`/`replay-verify` would report a clean, successful replay even though the recomputed `state_checkpoint_hash` (and, once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, `position_state_checkpoint_hash`) differs from the value bound into the accumulator-proven `TransactionInfo`. This undermines confidence in the integrity tooling itself and can mask a real state-commitment/hard-fork-class bug from being caught before or during incident response, since the audit path that is supposed to catch exactly this class of divergence is blind to it.

### Likelihood Explanation
This is not an attacker-triggered exploit against consensus; it is a structural correctness gap in the verification logic that is reachable any time the checkpoint hash actually diverges (a rare-but-catastrophic event: non-determinism bug, corrupted archive, or storage bug). Given the explicit TODO acknowledging the exact issue and enumerating the exact fields skipped, and given that `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/position-state roots are in active development to be verified via this exact path, the likelihood of this gap silently hiding a genuine divergence increases as those features are enabled on mainnet.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against recomputed values before declaring a match, gating each check on whether the corresponding feature (`HOT_STATE_ROOT_IN_TXN_INFO`, `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is enabled/known for that version, mirroring the existing TODO's guidance. This should be done and verified before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled on mainnet.

### Proof of Concept
Not applicable as a live exploit against consensus; the “PoC” is code-level: any transaction whose write set, events, gas, and status match the archived `TransactionInfo` bit-for-bit, but whose actual resulting checkpoint-boundary state root differs (e.g. due to a subtle divergence in JMT/hot-state/position-state summary computation), will pass `ensure_match_transaction_info` at [1](#0-0)  and thus pass through `replay_on_archive.rs`'s `execute_and_verify` [7](#0-6)  and `verify_execution` [4](#0-3)  without raising any error, despite the state root being provably wrong.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L692-705)
```rust
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
```

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L233-245)
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
```

**File:** execution/executor-types/src/ledger_update_output.rs (L92-114)
```rust
    pub fn ensure_transaction_infos_match(
        &self,
        transaction_infos: &[TransactionInfo],
    ) -> Result<()> {
        ensure!(
            self.transaction_infos.len() == transaction_infos.len(),
            "Lengths don't match. {} vs {}",
            self.transaction_infos.len(),
            transaction_infos.len(),
        );

        let mut version = self.first_version();
        for (txn_info, expected_txn_info) in
            zip_eq(self.transaction_infos.iter(), transaction_infos.iter())
        {
            ensure!(
                txn_info == expected_txn_info,
                "Transaction infos don't match. version:{version}, txn_info:{txn_info}, expected_txn_info:{expected_txn_info}",
            );
            version += 1;
        }
        Ok(())
    }
```
