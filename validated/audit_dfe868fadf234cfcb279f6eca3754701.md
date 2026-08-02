### Title
Replay/chunk-verification of `TransactionOutput` against `TransactionInfo` ignores state, hot-state, and native-position checkpoint hashes - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the routine used to validate that a locally re-executed (or peer-supplied) `TransactionOutput` matches an authenticated `TransactionInfo` (the object committed into the transaction accumulator and covered by ledger-info signatures). It checks status, gas used, write-set hash, and event root hash, but it never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` computes and compares only four fields against the committed `TransactionInfo`: execution status, gas used, write-set hash (`state_change_hash`), and event root hash. [2](#0-1) 

It explicitly, and admittedly, skips validating the checkpoint-hash fields that `TransactionInfoV1` carries: `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`. The code contains a direct acknowledgment of the gap:
```rust
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [3](#0-2) 

These checkpoint hashes are exactly the values that, per the feature-flag docstrings, are meant to be consensus/authenticated: `COMPUTE_TRADING_NATIVE_STATE_ROOTS` "commits [the native-position tree root] to `TransactionInfoV1`, so they are consensus-verified," and `HOT_STATE_ROOT_IN_TXN_INFO` "populates `TransactionInfoV1`'s hot state root hash, so it is committed to the ledger accumulator." [4](#0-3) 

The only consumer found in the indexed code that calls this comparator is `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes transactions and calls `executed_outputs[idx].ensure_match_transaction_info(...)` to decide whether the local re-execution matches the archived, ledger-info-signed `TransactionInfo`. [5](#0-4) 

Because the comparator silently ignores the checkpoint-hash fields, this tool (and any other code relying on `ensure_match_transaction_info` for correctness auditing) will report success even when the locally-computed state checkpoint root, hot-state root, or native-position state root diverges from what was actually committed to the accumulator and signed by validators. This is a genuine proof/commitment-integrity gap: the function's contract ("output matches transaction info") is violated for exactly the fields that are supposed to be authenticated on-chain state commitments.

I was unable to fully confirm from the indexed code whether this same comparator (or an equivalent unchecked path) is also used inside the live consensus/state-sync commit path (e.g., `execution/executor/src/chunk_executor/mod.rs`, which the search confirmed contains a call to `ensure_match_transaction_info` but whose file contents could not be retrieved due to index limits). If that call site is used to accept peer-provided `TransactionOutputListWithProof` chunks during state sync/fast-sync, the same missing checks would let a node accept and commit an output whose checkpoint hashes were never independently verified against the signed ledger info for those newer trading-native/hot-state trees, silently diverging that node's authenticated state root from the rest of the network.

### Impact Explanation
This falls in the "hard-fork-only divergence during commit, replay, restore, or proof verification" bucket: it is a broken proof/commitment-integrity invariant in code whose explicit purpose is to catch state divergence, and the code's own comment states the direct consequence — "replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution." A validator or auditor relying on this check to detect an execution/consensus bug involving the new trading-native/hot-state trees would get a false positive (successful replay verification) while the underlying committed root is actually wrong, defeating the very audit/replay mechanism meant to catch mainnet-consensus divergence for these newly-added authenticated roots.

### Likelihood Explanation
The gap is unconditional in the code path — it is not gated behind a feature flag check inside the function itself; it always skips these three fields regardless of whether `TRANSACTION_INFO_V1`, `HOT_STATE_ROOT_IN_TXN_INFO`, or `COMPUTE_TRADING_NATIVE_STATE_ROOTS` are enabled. It will only manifest once those features (which are documented as not yet fully enabled — the comment says "Validate the checkpoint hashes here **before** enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS") are turned on in a network, at which point every replay run silently loses coverage of the corresponding roots. The `TransactionOutputListWithProof::verify` used for authenticated client-facing API responses (a different, separate code path) does independently verify accumulator inclusion of `TransactionInfo` via `self.proof.verify(...)`, but does not check the internal consistency between output and the checkpoint-hash fields of `TransactionInfo` either. [6](#0-5) 

### Recommendation
Before shipping `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` to mainnet, extend `ensure_match_transaction_info` (and any equivalent output/txn-info comparator used on the chunk-executor/state-sync commit path) to independently recompute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` whenever those fields are present in the `TransactionInfo`, failing verification/replay on mismatch exactly as is already done for `state_change_hash` and `event_root_hash`.

### Proof of Concept
1. Enable `TRANSACTION_INFO_V1` + `COMPUTE_TRADING_NATIVE_STATE_ROOTS` on a chain.
2. Suppose a bug (in execution, position-tree update logic, or a malicious/misbehaving peer supplying transaction outputs) causes the locally re-executed `position_state_checkpoint_hash` to diverge from the one embedded in the archived, ledger-info-signed `TransactionInfo`.
3. Run `aptos-debugger aptos-db replay-on-archive` over the affected version range.
4. `execute_and_verify` calls `ensure_match_transaction_info`, which checks status/gas/write-set-hash/event-root-hash only — all of which can still match even though the position root differs. [5](#0-4) [7](#0-6) 
5. The tool reports "no failed transactions" / successful replay, even though the authenticated native-position state root actually diverged — masking a real ledger-state integrity issue.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2203)
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
```

**File:** types/src/transaction/mod.rs (L2940-3022)
```rust
    /// Verifies the transaction output list with proof using the given `ledger_info`.
    /// This method will ensure:
    /// 1. All transaction infos exist on the given `ledger_info`.
    /// 2. If `first_transaction_output_version` is None, the transaction output list is empty.
    ///    Otherwise, the list starts at `first_transaction_output_version`.
    /// 3. Events, gas, write set, status in each transaction output match the expected event root hashes,
    ///    the gas used and the transaction execution status in the proof, respectively.
    /// 4. The transaction hashes match those of the transaction infos.
    pub fn verify(
        &self,
        ledger_info: &LedgerInfo,
        first_transaction_output_version: Option<Version>,
    ) -> Result<()> {
        // Verify the first transaction output versions match
        ensure!(
            self.get_first_output_version() == first_transaction_output_version,
            "First transaction and output version ({:?}) doesn't match given version ({:?}).",
            self.get_first_output_version(),
            first_transaction_output_version,
        );

        // Verify the lengths of the transactions and outputs match the transaction infos
        ensure!(
            self.proof.transaction_infos.len() == self.get_num_outputs(),
            "The number of TransactionInfo objects ({}) does not match the number of \
             transactions and outputs ({}).",
            self.proof.transaction_infos.len(),
            self.get_num_outputs(),
        );

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

        Ok(())
    }
```

**File:** types/src/on_chain_config/aptos_features.rs (L203-209)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
    /// When enabled, execution populates `TransactionInfoV1`'s hot state root hash, so it
    /// is committed to the ledger accumulator. Requires `TRANSACTION_INFO_V1`.
    HOT_STATE_ROOT_IN_TXN_INFO = 123,
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
