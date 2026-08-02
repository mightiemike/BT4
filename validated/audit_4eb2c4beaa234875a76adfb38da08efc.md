I'll investigate the Aptos codebase for genuine integrity issues analogous to the pattern described (a value used in a security-critical calculation that doesn't account for "leftover" or excess amounts, leading to systematic overstatement/understatement).## Title
Replay-verify accepts corrupted/divergent state-checkpoint roots because `TransactionOutput::ensure_match_transaction_info` never compares `state_checkpoint_hash` (or hot/position checkpoint hashes) against the authenticated `TransactionInfo` - (File: `types/src/transaction/mod.rs`)

### Summary
`ensure_match_transaction_info` is the integrity check used by backup/replay tooling (`storage/db-tool/src/replay_on_archive.rs`, and the backup-cli transaction replay path) to confirm that a locally re-executed `TransactionOutput` matches the authenticated, accumulator-proven `TransactionInfo` pulled from a signed `LedgerInfo`. It checks status, gas, write-set hash, and event-root hash, but it explicitly skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the fields that actually commit the post-execution state root (JMT/SMT/hot-state root) into the ledger. This is called out in-code as a known gap.

### Finding Description
`TransactionInfo` binds five things to the accumulator: transaction hash, write-set hash (`state_change_hash`), event root, execution status/gas, and the state checkpoint hash(es) that represent the Merkle root of the entire account state after the checkpoint transaction. Only the checkpoint hash actually reflects the *cumulative* state (all prior writes applied through the Sparse/Jellyfish Merkle tree), whereas `state_change_hash` only reflects the hash of *this transaction's own* write set.

`ensure_match_transaction_info` (types/src/transaction/mod.rs, function at line 2139) performs:
- status check (2148-2157)
- gas check (2159-2166)
- write-set hash check (2168-2178)
- event-root check (2180-2195)

and then, at lines 2197-2202, contains an explicit TODO acknowledging it "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`)" and warns that "replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is the sole per-transaction correctness gate in `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` (lines 388-406), which re-executes backed-up transactions with `AptosVMBlockExecutor` and calls `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_infos[idx], ...)` where `expected_txn_infos` were already accumulator-proof-verified against a validator-signed `LedgerInfo`. Because the checkpoint hash is never compared, a locally computed state root that diverges from the canonical/authenticated one at a checkpoint boundary (e.g. due to a state-store/JMT/hot-state application bug, a sharded-total-supply aggregation bug, or a native-position-state divergence) is not detected: all four checked fields (status/gas/write-set-hash/event-root) can match individually per-transaction while the aggregate state root at the checkpoint is wrong, and the tool reports success.

### Impact Explanation
This breaks the "committed state that differs from the correct VM result... accepted" and "hard-fork-only divergence during commit, replay ... verification" state-integrity invariants. `replay_on_archive`/backup-cli replay-verify are the mechanisms operators and Aptos Labs itself rely on to certify that an archive/replay reproduces the canonical, validator-agreed ledger state. If the recomputed state root silently diverges (a real state-root bug that would otherwise indicate a consensus-relevant execution or storage discrepancy) it is masked, letting a corrupted ledger state pass verification as authentic. This can hide critical state-integrity bugs (e.g. an aggregator/state-store bug shipping a wrong `total_supply` or account balance root) from the tooling designed specifically to catch them, and could let a bootstrapped/restored node serve authenticated-looking but incorrect state.

### Likelihood Explanation
The gap is unconditional and always present in the current code path — it doesn't require any attacker-controlled input; it's a missing check that always executes. However, actually *triggering* an observable divergence requires some separate root-cause bug in state application (execution/storage) to produce a different state root while still reproducing identical per-transaction write sets/events/gas/status (plausible, since checkpoint hash aggregates the whole tree, not just this txn's writes). The report's own TODO frames this as guarding a future feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS` / native "position" state); until that feature is used, the primary risk is that regressions in state-root computation for that subsystem (or elsewhere) go undetected by replay-verify.

### Recommendation
Extend `ensure_match_transaction_info` to also assert that:
- `txn_info.state_checkpoint_hash()` matches the checkpoint hash computed by state checkpoint logic for this version (when the transaction is a checkpoint boundary), and
- `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()` match analogous locally computed values, when applicable/available to the caller.

This requires threading the actual computed checkpoint hash(es) into this function (or performing the comparison one layer up, in `execute_and_verify`/the replay-verify harness, where the state view after applying the chunk is available) rather than leaving it a silent no-op, before any feature (e.g. `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that relies on that state root is enabled.

### Proof of Concept
Conceptual PoC (cannot be executed without a live DB/backup, but the missing-check path is directly traceable):
1. Introduce (or trigger via an existing latent bug) a state-store/JMT/hot-state application discrepancy that changes the SMT root for a version without altering any individual transaction's write set, events, gas, or status (e.g. a bug in `update_total_supply`/aggregator delta application in `aptos-move/aptos-vm/src/sharded_block_executor/sharded_aggregator_service.rs`, or any hot-state/position-state application issue).
2. Run `aptos-db-tool replay-on-archive` (or backup-cli replay-verify) over the affected version range using a genuine backup whose `TransactionInfo`s carry the correct, canonical `state_checkpoint_hash`.
3. `execute_and_verify` (storage/db-tool/src/replay_on_archive.rs:388-406) calls `ensure_match_transaction_info`, which passes because status/gas/write-set-hash/event-root all still match.
4. The tool reports the replay as verified/successful even though the locally computed state checkpoint root differs from the canonical one — confirmed by the code comment at types/src/transaction/mod.rs:2197-2202 describing exactly this failure mode. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** types/src/transaction/mod.rs (L2261-2284)
```rust
    #[builder(finish_fn = build)]
    pub fn builder_v1(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        hot_state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
        position_state_checkpoint_hash: Option<HashValue>,
    ) -> Self {
        Self::V1(TransactionInfoV1::new(
            transaction_hash,
            state_change_hash,
            event_root_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            gas_used,
            status,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
        ))
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
