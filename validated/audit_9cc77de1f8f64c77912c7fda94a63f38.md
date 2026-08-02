### Title
`ensure_match_transaction_info` omits state/hot-state/position checkpoint hash validation, allowing `replay_on_archive`/`replay_verify` to report success on a diverged state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole equality check used by `db-tool`'s `replay_on_archive`/`replay_verify`, `aptos-debugger`, and the CLI's local-replay path to confirm a locally re-executed transaction matches the authenticated on-chain `TransactionInfo`. It compares status, gas, write-set hash (`state_change_hash`), and event root hash, but never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. [1](#0-0) 

### Finding Description
`TransactionInfoV1`/`V0` carry authenticated checkpoint hashes that summarize the periodic Sparse-Merkle world-state root (`state_checkpoint_hash`), the hot-state root (`hot_state_checkpoint_hash`), and — per an in-repo TODO — a "trading-native" position state root (`position_state_checkpoint_hash`), each committed into the transaction accumulator via `TransactionInfo`'s hash. [2](#0-1) 

`ensure_match_transaction_info` checks `status`, `gas_used`, the write-set hash against `state_change_hash`, and the event root hash against `event_root_hash`, then returns `Ok(())` without ever inspecting the checkpoint hash fields of `txn_info`. The function itself contains a TODO acknowledging this gap explicitly for the checkpoint hashes, including `position_state_checkpoint_hash`. [3](#0-2) 

This function is the terminal correctness check in `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes archived transactions with `AptosVMBlockExecutor` and calls `executed_outputs[idx].ensure_match_transaction_info(...)` as the pass/fail gate for the entire replay-verify tool; any mismatch found elsewhere is surfaced, but a checkpoint-root divergence is not. [4](#0-3) 

The same function is also used by `aptos-debugger`'s mismatch printer and the CLI's transaction-replay path as the authoritative comparator against authenticated on-chain `TransactionInfo`. [5](#0-4) [6](#0-5) 

By contrast, `TransactionOutputListWithProof::verify` (the path used to validate synced/fetched outputs against a ledger-info-rooted accumulator proof) also omits state-checkpoint-hash comparisons in its per-item checks, checking only events, write-set hash, gas, status, and transaction hash before validating the accumulator proof. [7](#0-6) 

### Impact Explanation
If local re-execution (via `AptosVMBlockExecutor`) produces a state/hot-state/position checkpoint root that diverges from the value actually committed and hashed into the on-chain `TransactionInfo` — e.g., due to a state-computation bug, a JMT/position-tree computation defect, or a version-skew in the checkpoint algorithm — `ensure_match_transaction_info` will still return `Ok(())` as long as status, gas, write-set hash, and event hash happen to match. `replay_on_archive`/`replay_verify` (the tool operators/auditors rely on to detect non-determinism or bugs across a chain history) would then falsely report a clean replay even though the authenticated state root diverges from local execution, undermining the state-integrity guarantee these tools exist to provide. This matches the report's bug class: an integrity check that omits a required term (here, checkpoint-hash fields) and silently accepts a state that should have been flagged as inconsistent.

### Likelihood Explanation
This is a self-acknowledged gap (see the in-code TODO) rather than a hypothetical one, and it directly affects the production replay-verification tooling path used by `db-tool`. The TODO is scoped to "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" for the `position_state_checkpoint_hash` field specifically, suggesting the position-checkpoint aspect may currently be dormant behind a feature flag that I could not confirm is disabled in this snapshot. However, the omission of `state_checkpoint_hash` and `hot_state_checkpoint_hash` (which are not gated by that TODO) appears to be a live, unconditional gap in the comparator regardless of that flag.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on either side) between the locally computed transaction output/state and `txn_info`, failing with a descriptive `ensure!` as done for the other fields, before enabling any feature that populates the position-checkpoint field.

### Proof of Concept
Not independently constructed — I was unable to trace an end-to-end mainnet-triggering scenario (e.g., confirming `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is presently enabled, or identifying a concrete code path that produces a genuinely divergent checkpoint hash) within available tool budget. The finding is grounded in the exact comparator code and its call sites shown above, but full exploitability confirmation (whether a divergence can currently occur and whether the affected feature is enabled on mainnet) requires further investigation than is possible with the available search tools. [8](#0-7)

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

**File:** types/src/transaction/mod.rs (L2440-2461)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct TransactionInfoV1 {
    gas_used: u64,
    status: ExecutionStatus,
    transaction_hash: HashValue,
    event_root_hash: HashValue,
    state_change_hash: HashValue,
    state_checkpoint_hash: Option<HashValue>,
    hot_state_checkpoint_hash: Option<HashValue>,
    auxiliary_info_hash: Option<HashValue>,

    /// Repurposed reserved field; `None` matches the prior BCS encoding.
    position_state_checkpoint_hash: Option<HashValue>,
    placeholder1: Option<HashValue>,
    placeholder2: Option<HashValue>,
    placeholder3: Option<HashValue>,
    placeholder4: Option<HashValue>,
    placeholder5: Option<HashValue>,
    placeholder6: Option<HashValue>,
    placeholder7: Option<HashValue>,
}
```

**File:** types/src/transaction/mod.rs (L2970-3019)
```rust
        // Verify the events, write set, status, gas used and transaction hashes.
        self.transactions_and_outputs.par_iter().zip_eq(self.proof.transaction_infos.par_iter())
        .map(|((txn, txn_output), txn_info)| {
            // Check the events against the expected events root hash
            verify_events_against_root_hash(&txn_output.events, txn_info)?;

            // Verify the write set matches for both the transaction info and output
            let write_set_hash = CryptoHash::hash(&txn_output.write_set);
            ensure!(
                txn_info.state_change_hash() == write_set_hash,
                "The write set in transaction output does not match the transaction info \
                     in proof. Hash of write set in transaction output: {}. Write set hash in txn_info: {}.",
                write_set_hash,
                txn_info.state_change_hash(),
            );

            // Verify the gas matches for both the transaction info and output
            ensure!(
                txn_output.gas_used() == txn_info.gas_used(),
                "The gas used in transaction output does not match the transaction info \
                     in proof. Gas used in transaction output: {}. Gas used in txn_info: {}.",
                txn_output.gas_used(),
                txn_info.gas_used(),
            );

            // Verify the execution status matches for both the transaction info and output.
            ensure!(
                *txn_output.status() == TransactionStatus::Keep(txn_info.status().clone()),
                "The execution status of transaction output does not match the transaction \
                     info in proof. Status in transaction output: {:?}. Status in txn_info: {:?}.",
                txn_output.status(),
                txn_info.status(),
            );

            // Verify the transaction hashes match those of the transaction infos
            let txn_hash = txn.committed_hash();
            ensure!(
                txn_hash == txn_info.transaction_hash(),
                "The transaction hash does not match the hash in transaction info. \
                     Transaction hash: {:x}. Transaction hash in txn_info: {:x}.",
                txn_hash,
                txn_info.transaction_hash(),
            );
            Ok(())
        })
        .collect::<Result<Vec<_>>>()?;

        // Verify the transaction infos are proven by the ledger info.
        self.proof
            .verify(ledger_info, self.get_first_output_version())?;
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

**File:** aptos-move/cli/src/commands.rs (L2651-2655)
```rust
                if !self.skip_comparison {
                    txn_output
                        .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                        .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
                }
```
