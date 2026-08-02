### Title
Replay-verification skips state/hot-state/position checkpoint hashes, letting committed state diverge undetected - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single authenticity gate used by replay-verify tooling (`storage/db-tool/src/replay_on_archive.rs`, chunk-executor's `ReplayChunkVerifier::verify_execution` in `execution/executor/src/chunk_executor/mod.rs`, and `aptos-debugger`'s `print_mismatches`) to confirm that locally re-executed transactions match the authenticated `TransactionInfo` pulled from backup/archive data. The function checks status, gas, write-set hash, and event-root hash, but explicitly and admittedly skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the very fields that summarize the aggregate Jellyfish Merkle / hot-state / position-state roots.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  validates the re-executed `TransactionOutput` against the authenticated `TransactionInfo` fetched from a trusted backup/ledger source. It checks:
- execution status
- gas used
- write-set hash (`state_change_hash`) — this is only the per-transaction write-set hash, not the accumulated state root
- event root hash

It never compares `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` against the locally computed checkpoint roots. This gap is called out directly in the code's own TODO comment: [2](#0-1) , which states that replay-verify tooling "can report a successful replay even when the authenticated position state root diverges from local execution."

This function is invoked from three integrity-relevant call sites:
- `execution/executor/src/chunk_executor/mod.rs` `verify_execution`, used by `ReplayChunkVerifier` during transaction replay: [3](#0-2) 
- `storage/db-tool/src/replay_on_archive.rs`, the CLI tool operators use to audit historical execution correctness against archived data
- `aptos-move/aptos-debugger/src/aptos_debugger.rs` `print_mismatches`: [4](#0-3) 

Because `state_checkpoint_hash` is the root hash of the Jellyfish Merkle Tree describing the entire world state at a checkpoint (as documented in `TransactionInfoV0`: [5](#0-4) ), it is the field that would catch a state-corruption bug in JMT construction, state accumulation, or any divergence between local execution's aggregate state and the historically-committed state — even when each individual transaction's own write set hash matches. A per-transaction write-set hash matching does not prove that the *aggregated* state tree (built incrementally over many transactions) was applied/hashed correctly. Any bug in state-tree merge logic, restore/replay accumulation, or hot-state/position-state root computation that corrupts the checkpoint root while leaving the individual write set unchanged is invisible to this verifier.

### Impact Explanation
This directly matches the "Proof and Storage Pivots" requirement: "Storage schemas, replay paths, and restore helpers must not reinterpret committed data into a different ledger state," and "Hard-fork-only divergence during commit, replay, restore, or proof verification." Replay-verify is the operational mechanism (used in CI via `testsuite/replay-verify/main.py` and `testsuite/smoke-test/src/storage.rs`, and by node operators via `db-tool`) that is supposed to catch exactly this class of divergence before/after hard forks or when validating archive data. With the checkpoint-hash comparison omitted, a state root divergence introduced by a bug elsewhere in the state-computation pipeline (JMT merge, hot-state/position-state root computation) would pass replay-verify silently, giving false confidence that historical execution reproduces the authenticated chain state. This is a high-severity gap in the state-integrity audit trail on which mainnet operators rely, even though it's not itself an exploit that corrupts consensus (validators still use `ensure_transaction_infos_match`, a stricter full-equality check, during normal execution/sync paths).

### Likelihood Explanation
The bug is not a theoretical possibility but an admitted, self-documented gap directly in the code (the TODO explicitly names the risk). It is definitely reachable any time `ensure_match_transaction_info` is used for replay/debug verification, which is a core, regularly-run auditing tool. The TODO also flags that this gap must be closed "before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS," implying the feature (native position/trading state roots) is being staged with this known hole.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, and where applicable `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`, against locally computed values whenever the transaction is a checkpoint boundary, before the code path (and `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is enabled for production replay-verify usage.

### Proof of Concept
Not directly exploitable as a live network attack; this is a verification-completeness gap. Demonstration: instrument a test where a `TransactionOutput`'s write set/events match a given `TransactionInfo` but is fed a `state_checkpoint_hash` deliberately mismatched with the actual JMT root produced by local execution (e.g., corrupt the checkpoint computation in `do_state_checkpoint.rs`, or feed a stale/incorrect `state_checkpoint_hashes` argument through `assemble_transaction_infos` at [6](#0-5) ). Calling `ensure_match_transaction_info` will return `Ok(())` despite the state roots diverging, confirming the check silently passes.

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

**File:** types/src/transaction/mod.rs (L2405-2412)
```rust
    /// The hash value summarizing all changes caused to the world state by this transaction.
    /// i.e. hash of the output write set.
    state_change_hash: HashValue,

    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,
```

**File:** execution/executor/src/chunk_executor/mod.rs (L692-697)
```rust
            if let Err(err) = txn_out.ensure_match_transaction_info(
                version,
                txn_info,
                Some(write_set),
                Some(events),
            ) {
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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L82-100)
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
```
