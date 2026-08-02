## Analysis Summary

I traced the external report's core theme (missing validation of security-critical parameters at a commit-defining boundary) to Aptos's transaction-info-based state-integrity check that gates replay/restore verification: `TransactionOutput::ensure_match_transaction_info` in [1](#0-0) .

### Title
Replay-verify integrity check omits state/hot-state/position checkpoint root validation, allowing corrupted state roots to pass as verified — (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used to authenticate a locally re-executed (or backup-restored) transaction output against an accumulator-proven `TransactionInfo` fetched from a trusted `LedgerInfo`. It validates execution `status`, `gas_used`, the write-set hash (`state_change_hash`) and the `event_root_hash`, but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that actually authenticate the resulting Jellyfish-Merkle / hot-state / position-state root produced after applying the write set. This gap is explicitly acknowledged in a `TODO(trading-native)` comment in the same function.

### Finding Description
`ensure_match_transaction_info` is called from the chunk executor's `verify_execution` path used during backup/archive replay-verification: [2](#0-1) . There, transactions are re-executed with the VM, and the resulting `TransactionOutput` is checked against `transaction_infos` supplied from the backup/archive being verified (`storage/backup/backup-cli/src/coordinators/replay_verify.rs`, `storage/db-tool/src/replay_verify.rs`). This same helper is also used by `aptos-move/cli` transaction replay and `aptos-debugger` mismatch reporting (`aptos_debugger.rs:233-246`).

The check explicitly skips the checkpoint-hash fields: [3](#0-2) 

Those checkpoint hashes are precisely the fields that bind a `TransactionInfo` (which itself is proven via the `TransactionAccumulator`/`LedgerInfo`, see `TransactionInfoListWithProof::verify` in [4](#0-3) ) to the actual on-disk state root (JMT root, hot-state root, native-position root) computed by `DoStateCheckpoint` (`execution/executor/src/workflow/do_state_checkpoint.rs`) and used to build `TransactionInfo` at commit time in `assemble_transaction_infos` (`execution/executor/src/workflow/do_ledger_update.rs:58-126`), which does populate `maybe_state_checkpoint_hash`, `maybe_hot_state_checkpoint_hash`, `maybe_position_state_checkpoint_hash`.

Because `state_change_hash` only equals `CryptoHash::hash(write_set)` (a hash of the abstract per-transaction write operations, not of the resulting merklized state), a bug anywhere in state-tree construction (JMT node hashing, hot-state root aggregation, sharded state-tree combination, or the native-position tree) that produces a wrong root from a correct write set would still pass `ensure_match_transaction_info`, since none of the three checkpoint-hash fields are compared.

### Impact Explanation
This breaks the invariant that "authenticated proof-bearing responses/tooling must stay bound to the right ledger root." The replay-verify and CLI-replay tooling exist specifically to catch state divergence between locally computed state and the authenticated on-chain state (this is their entire purpose for archive/backup integrity and node operators auditing state-tree correctness). With this gap, a latent state-tree computation bug (in `DoStateCheckpoint`, hot-state, or native-position summary logic) can corrupt the durable state root while replay-verify reports success, because the only thing checked is that the write set and events reproduce byte-for-byte — the actual merklized root used for state proofs and light-client verification is never cross-checked against the ledger-info-authenticated value during verification. This is a real gap in the state-commitment integrity chain relied upon by restore/replay-verify flows.

### Likelihood Explanation
Moderate: this requires either an actual latent bug in the state-tree materialization code, or divergent behavior across node builds/versions in a hard fork/rollout scenario (e.g., differing hot-state or native-position feature flags) — exactly the class of "hard-fork-only divergence during ... replay ... or proof verification" impacts the assignment scopes in. The code's own TODO comment confirms the aptos-core team is aware this comparator does not perform this validation, and the gate `compute_trading_native_state_roots`/hot-state feature paths are actively evolving, increasing the chance of exactly the state-tree-vs-write-set divergence this check should — but currently does not — catch.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`-derived checkpoint hashes (when the specific `TransactionOutput`/execution context indicates a checkpoint boundary) against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()`, threading through the locally computed state-summary roots (as already computed by `DoStateCheckpoint`) to the verification call sites in `chunk_executor::verify_execution`, `aptos-debugger`, and CLI replay before treating a replay/verify pass as authoritative.

### Proof of Concept
1. Introduce (or trigger via an existing latent bug) a divergence solely in state-tree materialization — e.g., a JMT internal-node hashing edge case, hot-state root aggregation error, or native-position tree combination bug — such that applying the *same* write set produces a different Merkle root than what is recorded on-chain, while the write set, events, gas, and status remain identical.
2. Run `db-tool replay-verify` (or `backup-cli replay_verify`) against the affected version range.
3. `chunk_executor::verify_execution` re-executes the transactions, computes a `TransactionOutput` whose write set/events/gas/status match the archived `TransactionInfo`, and calls `ensure_match_transaction_info`, which returns `Ok(())` despite the state root being wrong.
4. The tool reports the range as successfully verified even though the resulting state-tree root diverges from the ledger-info-authenticated `state_checkpoint_hash`.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L684-707)
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
        Ok(end_version)
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
