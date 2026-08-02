## Title
`ensure_match_transaction_info` never validates state/hot-state/position checkpoint hashes, allowing replay-verify to accept a corrupted state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single authenticity gate used both during `TransactionReplayer::verify_execution` (chunk-executor replay path) and by CLI/debugger tooling to assert that a freshly re-executed transaction matches the previously committed, ledger-proof-bound `TransactionInfo`. The function checks status, gas, write-set hash and event-root hash, but explicitly and intentionally skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — with a `TODO(trading-native)` comment admitting this gap. [1](#0-0) [2](#0-1) 

### Finding Description
`verify_execution` in the chunk executor's replay path calls this exact function to gate whether re-executed output matches the backed-up/authenticated `TransactionInfo` before the chunk is accepted for replay-based restore: [3](#0-2) 

This is the code path used by `db-tool`'s `replay-verify` and `TransactionRestoreController`/`ReplayVerifyCoordinator` when reconstructing/verifying an archive against a target ledger: [4](#0-3) [5](#0-4) 

Because `ensure_match_transaction_info` never compares `self.state_checkpoint_hash()`-equivalent local computation against `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`, a local execution that produces a *different* state-checkpoint root (main state SMT root, hot-state root, or the new native-position Merkle root) than what is recorded in the authenticated `TransactionInfo` will still pass verification, as long as write-set bytes, event hashes, gas, and status happen to match. The comment in the source explicitly states this outcome: *"replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution."*

The broken invariant is exactly the "Proof And Storage Pivots" requirement that *"VM outputs, transaction infos, events, and write sets must survive executor-to-storage handoff unchanged"* and that *"restore paths must preserve deterministic proof binding"*. Here the state-checkpoint/root-hash component of `TransactionInfo` — the very field that is Merkle-committed into the transaction accumulator and ultimately signed by validators in the `LedgerInfo` — is silently excluded from the one function whose job is to assert local-vs-authenticated equivalence during replay.

### Impact Explanation
The main-state `state_checkpoint_hash` is populated on essentially every checkpoint transaction and is part of the accumulator-committed `TransactionInfo`; a replay/backup-verify run that reconstructs a DB (or that an operator uses to validate an archive/snapshot) could report success while the underlying state root diverges from what consensus actually committed. The newer `hot_state_checkpoint_hash` and `position_state_checkpoint_hash` fields (native-position / trading-native state) are gated behind on-chain feature flags (`HOT_STATE_ROOT_IN_TXN_INFO`, `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that are not yet enabled by default, which limits current exploitability, but the check for the always-active `state_checkpoint_hash` field is also missing in this exact same code path — meaning even without any new feature flags, a locally-diverging state root during replay is not detected by `ensure_match_transaction_info`. This is a real gap in the state-integrity guarantee that replay/restore tooling is meant to provide, though it requires that a divergence in local VM execution (e.g., from a bug elsewhere, a bit-for-bit different DB state, or non-determinism) already exist to be masked; this function does not itself corrupt state, it fails to *detect* corruption that write-set/event checks alone cannot catch (e.g. dropped writes that still hash the same via a bug in write-set hashing, or divergent underlying persisted state that a state-root check would have caught).

### Likelihood Explanation
Medium: this is not a directly attacker-triggerable state-corruption bug on a single honest full node executing consensus-agreed blocks (block-executor's `DoStateCheckpoint` still recomputes and asserts against `known_state_checkpoints` in the online/consensus path via `chunk_result_verifier.rs`'s `ensure_transaction_infos_match`, which is a separate function). The exposure is specifically in the replay-verify / debugger / db-tool CLI paths that rely on `ensure_match_transaction_info`, used for auditing backups and archives offline. An operator or auditor trusting `replay-verify`'s "success" result for state-root integrity would get a false assurance if local re-execution's state-checkpoint (or, once enabled, hot-state/position) root differs from the archived `TransactionInfo`.

### Recommendation
Extend `ensure_match_transaction_info` to recompute the local state-checkpoint hash (and, when applicable, hot-state and position-state checkpoint hashes) and assert equality against `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()`, exactly as the TODO comment states, before any feature that produces these roots (in particular `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is enabled on mainnet.

### Proof of Concept
No standalone PoC is provided because the divergence itself (a locally different state-checkpoint root than the archived `TransactionInfo`) requires an independent root cause (e.g., a state-store bug, non-determinism, or corrupted local DB) that is out of scope for this analysis. The concrete, provable finding is the code-level fact that `ensure_match_transaction_info` at [6](#0-5)  omits `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` comparisons, and is invoked as the sole gate in the replay path at [7](#0-6) , which is reachable from the `db-tool replay-verify` CLI at [4](#0-3) .

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

**File:** storage/db-tool/src/replay_verify.rs (L62-87)
```rust
impl Opt {
    pub async fn run(self) -> Result<()> {
        let restore_handler = Arc::new(AptosDB::open_kv_only(
            StorageDirPaths::from_path(self.db_dir),
            false,                       /* read_only */
            NO_OP_STORAGE_PRUNER_CONFIG, /* pruner config */
            self.rocksdb_opt.into(),
            BUFFERED_STATE_TARGET_ITEMS,
            DEFAULT_MAX_NUM_NODES_PER_LRU_CACHE_SHARD,
            None,
        )?)
        .get_restore_handler();
        let ret = ReplayVerifyCoordinator::new(
            self.storage.init_storage().await?,
            self.metadata_cache_opt,
            self.trusted_waypoints_opt,
            self.concurrent_downloads.get(),
            self.replay_concurrency_level.get(),
            restore_handler,
            self.start_version.unwrap_or(0),
            self.end_version.unwrap_or(Version::MAX),
            self.validate_modules,
            VerifyExecutionMode::verify_except(self.txns_to_skip).set_lazy_quit(self.lazy_quit),
        )?
        .run()
        .await;
```

**File:** storage/backup/backup-cli/src/coordinators/replay_verify.rs (L84-98)
```rust
    pub async fn run(self) -> Result<(), ReplayError> {
        info!("ReplayVerify coordinator started.");
        let ret = self.run_impl().await;

        if let Err(e) = &ret {
            error!(
                error = ?e,
                "ReplayVerify coordinator failed."
            );
        } else {
            info!("ReplayVerify coordinator exiting with success.");
        }

        ret
    }
```
