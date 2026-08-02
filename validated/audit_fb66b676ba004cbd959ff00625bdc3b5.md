## Finding

The Aptos-native analog to the `LeverageManager` bug is a self-documented gap in `TransactionOutput::ensure_match_transaction_info` (used by chunk-executor replay verification and `aptos-debugger`/CLI replay tooling): it verifies status, gas, `write_set` hash, and event root hash against a `TransactionInfo`, but never compares the computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the corresponding fields of the (authenticated) `TransactionInfo`.

### Title
Replay-verify skips comparing state/hot-state/position-state checkpoint roots, allowing divergent committed state to pass verification - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole correctness gate used both by the chunk executor's replay-verification path and by CLI/debugger replay tooling to confirm that a locally re-executed transaction reproduces the authenticated on-chain result. [1](#0-0)  It checks execution status, gas used, the write-set hash (`state_change_hash`), and the event root hash, but the function returns `Ok(())` without ever comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap explicitly called out in the trailing comment. [2](#0-1) 

### Finding Description
`TransactionInfoV1` carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as the authenticated roots of the periodic SMT/JMT state checkpoints. [3](#0-2)  This function is invoked by `TransactionReplayer::verify_execution` in the chunk executor, which re-executes a range of transactions and calls `ensure_match_transaction_info` against the (previously authenticated) `transaction_infos` to decide whether the locally computed output matches what was actually committed to the ledger. [4](#0-3)  The same function is used by the CLI/debugger replay commands. [5](#0-4) 

Because only the write-set hash (a hash of the raw per-transaction write set) is checked, and not the periodic checkpoint roots (`state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`), any divergence introduced downstream of write-set materialization — e.g., in state-checkpoint construction, hot-state tree updates, or the native-position tree (`DoStateCheckpoint`/`get_position_checkpoint_hashes` path) [6](#0-5)  — will not be caught by replay verification, even though the write sets themselves matched.

### Impact Explanation
Replay-verify (`storage/db-tool/src/replay_verify.rs`, chunk-executor `verify_execution`) is the primary tool used to certify that historical backups/ledger data are authentic and that re-execution reproduces the exact committed ledger state, including state roots. [7](#0-6)  Because checkpoint-root comparisons are skipped, replay-verify can report success ("ReplayVerify coordinator succeeded") even when the locally recomputed state checkpoint / hot-state / position-state root diverges from the authenticated on-chain root — i.e., accepting a state commitment that differs from the correct VM result as verified. This directly matches the required state-integrity gate: "Committed state that differs from the correct VM result... accepted as valid" and "Authenticated API or state-view output bound to the wrong version, object, or proof context" is effectively unchecked for the state-root dimension.

### Likelihood Explanation
This triggers deterministically whenever replay-verify or CLI replay is used on data where the state/hot-state/position-state root diverges but the write-set hash still matches (e.g., a bug in one of the checkpoint-construction paths, a corrupted archive, or in the future once `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/position-state-root feature is enabled) — no attacker action or privileged access is required to hit the gap; it's a validation omission in the checking code itself. The comment in the code confirms this is a known, currently-open gap rather than a hypothetical. [8](#0-7) 

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`'s recomputed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when available/applicable) against `txn_info`'s corresponding fields, failing verification on any mismatch, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or any dependent feature is enabled on mainnet.

### Proof of Concept
Not directly exploitable by an external attacker without a divergence source; the "PoC" is structural: construct a `TransactionOutput` whose write set matches `txn_info.state_change_hash()` but whose downstream checkpoint construction (position-state or hot-state) would compute a different root than `txn_info.state_checkpoint_hash()/hot_state_checkpoint_hash()/position_state_checkpoint_hash()`, then observe `ensure_match_transaction_info` at [9](#0-8)  return `Ok(())` despite the mismatch, and that this return value is what gates chunk-executor's `verify_execution` success/failure at [10](#0-9) .

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

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L98-106)
```rust
    )> {
        let _timer = OTHER_TIMERS.timer_with(&["get_position_checkpoint_hashes"]);

        let num_txns = execution_output.to_commit.len();
        let first_version = execution_output.first_version;
        let last_checkpoint_index = execution_output
            .to_commit
            .state_update_refs()
            .last_inner_checkpoint_index();
```

**File:** storage/db-tool/src/replay_verify.rs (L62-104)
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
        match ret {
            Err(e) => match e {
                ReplayError::TxnMismatch => {
                    info!("ReplayVerify coordinator exiting with Txn output mismatch error.");
                    process::exit(2);
                },
                _ => {
                    info!("ReplayVerify coordinator exiting with error: {:?}", e);
                    process::exit(1);
                },
            },
            _ => {
                info!("ReplayVerify coordinator succeeded");
            },
        };
        Ok(())
    }
```
