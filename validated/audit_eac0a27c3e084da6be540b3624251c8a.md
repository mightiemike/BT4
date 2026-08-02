## Finding

### Title
`ensure_match_transaction_info` never validates `state_checkpoint_hash` (or `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`), letting replay-verify and restore's execution-verification silently accept a wrong state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the canonical comparator used across Aptos tooling to confirm that a *locally re-executed* transaction output matches the transaction info that was already cryptographically committed to the ledger (i.e., proven by the accumulator and validator signatures). It checks status, gas used, the write-set hash (`state_change_hash`), and the event root hash, but it never compares the recomputed state-tree root against `txn_info.state_checkpoint_hash()` (nor `hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()`), even though the function receives the full `txn_info` and is explicitly meant to validate execution correctness. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` performs these checks and stops:
- status match
- gas_used match
- `write_set_hash == txn_info.state_change_hash()`
- `event_root_hash == txn_info.event_root_hash()` [2](#0-1) 

It then returns `Ok(())` with a TODO acknowledging the gap: [3](#0-2) 

This function is the sole correctness gate in three production-relevant code paths that re-execute historical transactions and are supposed to detect divergence between local computation and the already-committed, signature-authenticated ledger state:

1. `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, the core of Aptos's mainnet **replay-verify** tooling (referenced by `testsuite/replay-verify/README.md`, which runs against testnet/mainnet archives). [4](#0-3) 

2. `execution/executor/src/chunk_executor/mod.rs`'s `verify_execution`, used during backup **restore** when `VerifyExecutionMode` requires re-execution verification. [5](#0-4) 

3. `aptos-move/aptos-debugger` and `aptos-move/cli` transaction-replay commands. [6](#0-5) 

`state_checkpoint_hash` is the root of the global Sparse Merkle Tree state (world state) and is populated for every checkpoint/block-boundary transaction on `TransactionInfoV0`/`V1` today (not gated by any unreleased feature flag), so this gap is not limited to the not-yet-enabled trading-native/hot-state fields called out in the TODO — the primary state commitment itself is unchecked. [7](#0-6) 

This is the direct analog of the external report's `hashAssignment()` bug class: a field that determines the correctness of block execution (`metaHash` there, `state_checkpoint_hash` here) is excluded from the integrity check that is supposed to bind execution results to what was actually committed, allowing a real divergence to pass unnoticed by the very mechanism designed to catch it.

### Impact Explanation
Replay-verify and restore's execution-verification mode exist specifically to catch **hard-fork-causing bugs**: cases where new/candidate node software computes a different world state than what the real network already committed and signed. Because `ensure_match_transaction_info` never compares the recomputed state-checkpoint root to the authenticated `txn_info.state_checkpoint_hash()`, a genuine state-computation divergence (e.g., a bug in Sparse Merkle Tree update logic, a non-deterministic VM change, or a bad on-chain-config interpretation) would be reported as "verified"/"replayed successfully" even though the locally computed ledger state is wrong. This directly matches the in-scope impact "Hard-fork-only divergence during commit, replay, restore, or proof verification": the safety net meant to catch such divergence before it reaches production is defeated silently.

### Likelihood Explanation
This triggers whenever any code path exercises `ensure_match_transaction_info` with a real state-checkpoint divergence — i.e., whenever there is an actual VM/state-tree correctness bug being introduced (which is exactly the failure mode replay-verify is designed to detect during CI/CD before a release reaches mainnet, and that DB restore's execution-verification is designed to detect when rebuilding a node from backups). No attacker action or privileged access is required; the bug is a latent gap in an unprivileged, deterministic verification routine that silently downgrades "state divergence" to "no error."

### Recommendation
Extend `ensure_match_transaction_info` to recompute the local `state_checkpoint_hash` (and `hot_state_checkpoint_hash`/`position_state_checkpoint_hash` when applicable) from the local state view and assert equality against the values in `txn_info`, mirroring the existing `write_set_hash`/`event_root_hash` checks, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` and as a general hardening of replay-verify/restore correctness for the already-active `state_checkpoint_hash`.

### Proof of Concept
1. Introduce (hypothetically, for testing) a state-tree computation change that alters the computed state root for some transaction without altering the write-set bytes/hash (e.g., a bug affecting how the SMT is updated but not what's placed in the write set).
2. Run `storage/db-tool/src/replay_on_archive.rs` (`Verifier::verify` → `execute_and_verify` → `ensure_match_transaction_info`) or restore with `VerifyExecutionMode` enabled over a backup containing the affected version. [8](#0-7) 
3. Observe that despite the local `state_checkpoint_hash` differing from the trusted `expected_txn_infos[idx].state_checkpoint_hash()`, `ensure_match_transaction_info` returns `Ok(())` because it never compares that field, and the tool reports the range as successfully replayed/verified.

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

**File:** types/src/transaction/mod.rs (L2405-2416)
```rust
    /// The hash value summarizing all changes caused to the world state by this transaction.
    /// i.e. hash of the output write set.
    state_change_hash: HashValue,

    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,

    /// The hash value summarizing PersistedAuxiliaryInfo.
    auxiliary_info_hash: Option<HashValue>,
}
```

**File:** storage/db-tool/src/replay_on_archive.rs (L349-405)
```rust
    fn execute_and_verify(
        &self,
        executor: &AptosVMBlockExecutor,
        current_version: &mut Version,
        cur_txns: &mut Vec<Transaction>,
        cur_persisted_aux_info: &mut Vec<PersistedAuxiliaryInfo>,
        expected_txn_infos: &mut Vec<TransactionInfo>,
        expected_events: &mut Vec<Vec<ContractEvent>>,
        expected_writesets: &mut Vec<WriteSet>,
    ) -> Result<Option<Error>> {
        if cur_txns.is_empty() {
            return Ok(None);
        }
        let txns = cur_txns
            .iter()
            .map(|txn| SignatureVerifiedTransaction::from(txn.clone()))
            .collect::<Vec<_>>();
        let txns_provider = DefaultTxnProvider::new(
            txns,
            cur_persisted_aux_info
                .iter()
                .map(|info| AuxiliaryInfo::new(*info, None))
                .collect(),
        );
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
