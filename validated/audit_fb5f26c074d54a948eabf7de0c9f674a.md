## Title
Native-position write path bypasses the umbrella `TRADING_NATIVE` gate — (File: `aptos-move/framework/position-natives/src/natives.rs`)

### Summary
The comment on `TRADING_NATIVE` in `features.move` explicitly documents the invariant "Both must be on to write" — i.e. both the umbrella flag (`TRADING_NATIVE`, const 118) and the per-store flag (`NATIVE_POSITION`, const 119) must be enabled for a native-position write to occur. [1](#0-0) 

However, the actual Rust-side gate that runs immediately before a write is staged into the `VMChangeSet` — `check_feature_enabled` in `position-natives/src/natives.rs`, invoked by both `native_set_position` and `native_delete_position` — checks only `FeatureFlag::NATIVE_POSITION` and never checks `FeatureFlag::TRADING_NATIVE`: [2](#0-1) [3](#0-2) [4](#0-3) 

### Finding Description
`is_trading_native_enabled()`/`is_native_position_enabled()` are meant to jointly gate whether a write set may contain native-position deltas, per the documented invariant at `features.move` line 904-925. [1](#0-0) 

The only enforcement point directly adjacent to the write itself — `check_feature_enabled` — checks `FeatureFlag::NATIVE_POSITION` alone. Once this check passes, `native_set_position`/`native_delete_position` stage the write unconditionally via `NativePositionContext::stage_set`/`stage_delete`, which later becomes a `WriteOp` in the `position_write_set` bucket of `VMChangeSet` and is committed by `commit_native_position` in `aptosdb_writer.rs`. [5](#0-4) [6](#0-5) 

The only other gate in the call chain is the Move-level `trading_native_capability::assert_valid(cap)` invoked from the public wrappers `native_position::set_position`/`delete_position`. [7](#0-6) 
Based on the test names in `trading_native_capability.move` (`test_get_capability_requires_trading_native_flag`, `test_assert_valid_passes_when_active`, `test_held_cap_invalidated_by_deny`), `TRADING_NATIVE` appears to be checked at `get_capability` (capability-minting) time, and the only *revocation* path exercised for an already-held capability is exchange `deny`, not disabling `TRADING_NATIVE`. I could not fully load the body of `assert_valid`/`get_capability` in this session (index truncated most of the file to blank lines), so I cannot confirm with certainty whether `assert_valid` re-checks the live `TRADING_NATIVE` flag on every call or only at mint time. This is a gap in my verification, not a claim of certainty either way.

What is certain and verified directly from source: the native Rust function that actually performs the state write — the last line of defense before the write set is constructed — does not check `TRADING_NATIONAL`/`TRADING_NATIVE` at all. This means the documented "Both must be on to write" invariant is not enforced at the point where the write happens; it depends entirely on an upstream Move-side capability check whose per-call re-validation of the umbrella flag is unverified from available context.

### Impact Explanation
If a `TradingNativeCapability` was minted while `TRADING_NATIVE` was enabled, and an operator later disables only `TRADING_NATIVE` (leaving `NATIVE_POSITION` enabled) intending to kill-switch the whole subsystem while keeping the per-store flag semantics documented/reserved, any already-issued capability holder can still call `set_position`/`delete_position` and produce native-position write-set deltas — directly contradicting the documented invariant and corrupting the committed write set with state changes that should be unreachable at that feature configuration. This affects `commit_native_position` writes and the native-position Merkle tree/state root computation path. [8](#0-7) 

### Likelihood Explanation
This requires: (a) `NATIVE_POSITION` enabled, (b) `TRADING_NATIVE` disabled after having been enabled long enough for at least one capability to be minted, and (c) an account/exchange holding a previously-minted, non-denied capability. This is a plausible governance-flag-toggle sequence rather than a purely operator-error scenario, since the umbrella flag is explicitly documented as a kill switch independent of the per-store flags, and the code that should enforce it at the write site does not.

### Recommendation
Add an explicit `FeatureFlag::TRADING_NATIVE` check in `check_feature_enabled` (`aptos-move/framework/position-natives/src/natives.rs`) alongside the existing `NATIVE_POSITION` check, so the umbrella and per-store flags are both enforced at the actual write site, not only (possibly) at capability-mint time. Additionally, confirm/ensure `trading_native_capability::assert_valid` re-checks `is_trading_native_enabled()` on every call, not only at `get_capability`, so already-minted capabilities lose validity immediately when the umbrella flag is turned off.

### Proof of Concept
1. Enable `NATIVE_POSITION` (119) but leave `TRADING_NATIVE` (118) disabled (or mint a capability while `TRADING_NATIVE` is on, then disable it).
2. Submit a transaction calling `native_position::set_position` with a previously-obtained `TradingNativeCapability`.
3. Observe that `check_feature_enabled` in `position-natives/src/natives.rs` only checks `NATIVE_POSITION` and passes, allowing `native_set_position` to stage the write via `NativePositionContext::stage_set`. [2](#0-1) 
4. Inspect the resulting `VMChangeSet`/committed write set: it contains a native-position delta even though `is_trading_native_enabled()` is `false`, violating the documented "Both must be on to write" invariant.

### Citations

**File:** aptos-move/framework/move-stdlib/sources/configs/features.move (L904-925)
```text
    /// Umbrella auth flag for the native-trading subsystem; the per-store
    /// flags below gate the actual writes. Both must be on to write.
    const TRADING_NATIVE: u64 = 118;

    public fun get_trading_native_feature(): u64 {
        TRADING_NATIVE
    }

    public fun is_trading_native_enabled(): bool {
        is_enabled(TRADING_NATIVE)
    }

    /// Gates native-position writes.
    const NATIVE_POSITION: u64 = 119;

    public fun get_native_position_feature(): u64 {
        NATIVE_POSITION
    }

    public fun is_native_position_enabled(): bool {
        is_enabled(NATIVE_POSITION)
    }
```

**File:** aptos-move/framework/position-natives/src/natives.rs (L34-46)
```rust
fn check_feature_enabled(context: &SafeNativeContext) -> SafeNativeResult<()> {
    if context
        .get_feature_flags()
        .is_enabled(FeatureFlag::NATIVE_POSITION)
    {
        Ok(())
    } else {
        Err(SafeNativeError::Abort {
            abort_code: E_FEATURE_DISABLED,
            abort_message: Some("NATIVE_POSITION feature flag is not enabled".to_string()),
        })
    }
}
```

**File:** aptos-move/framework/position-natives/src/natives.rs (L61-80)
```rust
fn native_set_position(
    context: &mut SafeNativeContext,
    _ty_args: &[Type],
    mut args: VecDeque<Value>,
) -> SafeNativeResult<SmallVec<[Value; 1]>> {
    check_feature_enabled(context)?;
    let position_val = args.pop_back().ok_or_else(|| missing_arg("position"))?;
    let account: AccountAddress = safely_pop_arg!(args, AccountAddress);
    let market: AccountAddress = safely_pop_arg!(args, AccountAddress);
    let exchange: AccountAddress = safely_pop_arg!(args, AccountAddress);
    let native_pos = move_value_to_position(position_val)?;
    let ctx = context.extensions().get::<NativePositionContext>();
    let key = TradingNativeKey::Position {
        exchange,
        account,
        market,
    };
    ctx.stage_set(key, native_pos);
    Ok(smallvec![])
}
```

**File:** aptos-move/framework/position-natives/src/natives.rs (L82-99)
```rust
fn native_delete_position(
    context: &mut SafeNativeContext,
    _ty_args: &[Type],
    mut args: VecDeque<Value>,
) -> SafeNativeResult<SmallVec<[Value; 1]>> {
    check_feature_enabled(context)?;
    let account: AccountAddress = safely_pop_arg!(args, AccountAddress);
    let market: AccountAddress = safely_pop_arg!(args, AccountAddress);
    let exchange: AccountAddress = safely_pop_arg!(args, AccountAddress);
    let ctx = context.extensions().get::<NativePositionContext>();
    let key = TradingNativeKey::Position {
        exchange,
        account,
        market,
    };
    ctx.stage_delete(key);
    Ok(smallvec![])
}
```

**File:** aptos-move/framework/position-natives/src/context.rs (L48-61)
```rust
    pub fn stage_set(&self, key: TradingNativeKey, value: NativePosition) {
        POSITION_SETS_STAGED.fetch_add(1, Ordering::Relaxed);
        self.cache.borrow_mut().staged.insert(key, Some(value));
    }

    pub fn stage_delete(&self, key: TradingNativeKey) {
        POSITION_DELETES_STAGED.fetch_add(1, Ordering::Relaxed);
        self.cache.borrow_mut().staged.insert(key, None);
    }

    /// Drain the staged writes for routing into the VMChangeSet bucket.
    pub fn into_change_maps(self) -> BTreeMap<TradingNativeKey, Option<NativePosition>> {
        self.cache.into_inner().staged
    }
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L343-376)
```rust
    fn commit_native_position(&self, chunk: &ChunkToCommit, sync_commit: bool) -> Result<()> {
        let _timer = OTHER_TIMERS_SECONDS.timer_with(&["commit_native_position"]);
        let Some(bundle) = self.position.as_ref() else {
            return Ok(());
        };
        if chunk.transaction_outputs.is_empty() {
            return Ok(());
        }
        let committer = NativeStateCommitter::new(bundle.kv_db.clone());

        let chunk_first = chunk.first_version;
        let chunk_last_inclusive = chunk_first + chunk.transaction_outputs.len() as Version - 1;

        // Persist the position KV values + stale index.
        let mut sharded_kv_batches = new_sharded_kv_batches();
        let mut in_chunk_prior = InChunkPriorVersions::new();
        for (i, output) in chunk.transaction_outputs.iter().enumerate() {
            let version = chunk_first + i as Version;
            let position_writes: Vec<_> = output
                .write_set()
                .native_position_iter()
                .map(|(k, op)| (k.clone(), op.as_write_op().clone()))
                .collect();
            if !position_writes.is_empty() {
                committer
                    .apply(
                        version,
                        position_writes,
                        &mut sharded_kv_batches,
                        &mut in_chunk_prior,
                    )
                    .map_err(|e| AptosDbError::Other(format!("native commit: {e}")))?;
            }
        }
```

**File:** aptos-move/framework/aptos-experimental/sources/trading/position/native_position.move (L13-31)
```text
    public fun set_position(
        cap: &TradingNativeCapability,
        market: address,
        account: address,
        position: Position,
    ) {
        trading_native_capability::assert_valid(cap);
        native_set_position(trading_native_capability::exchange(cap), market, account, position);
    }

    /// Delete the position at `(exchange, market, account)`.
    public fun delete_position(
        cap: &TradingNativeCapability,
        market: address,
        account: address,
    ) {
        trading_native_capability::assert_valid(cap);
        native_delete_position(trading_native_capability::exchange(cap), market, account);
    }
```

**File:** storage/aptosdb/src/native_state_committer.rs (L60-140)
```rust
impl NativeStateCommitter {
    pub fn new(position_db: Arc<PositionDb>) -> Self {
        Self { position_db }
    }

    /// Accumulate one transaction's Position writes into the
    /// per-chunk batches. The caller commits once per chunk via
    /// `PositionDb::commit`.
    pub fn apply<P>(
        &self,
        version: Version,
        position_writes: P,
        sharded_kv_batches: &mut PositionShardedKvBatches,
        in_chunk_prior: &mut InChunkPriorVersions,
    ) -> Result<NativeMerkleLeafUpdates>
    where
        P: IntoIterator<Item = (StateKey, WriteOp)>,
    {
        let mut position_merkle: Vec<MerkleLeafUpdate> = Vec::new();
        for (state_key, op) in position_writes {
            match state_key.inner() {
                StateKeyInner::TradingNative(TradingNativeKey::Position { .. }) => (),
                other => {
                    return Err(AptosDbError::Other(format!(
                        "position_write_set contained non-Position StateKey: {other:?}"
                    )));
                },
            };
            let maybe_value = op.as_state_value_opt().cloned();
            let kind_label = if maybe_value.is_some() {
                "upsert"
            } else {
                "delete"
            };
            POSITION_WRITES.with_label_values(&[kind_label]).inc();
            let state_key_hash = state_key.hash();
            let shard = ShardedKvDb::shard_of_state_key(&state_key);
            let pos_batch = sharded_kv_batches[shard].get_or_insert_with(SchemaBatch::new);

            // In-chunk map first (same-chunk earlier writes), then DB.
            let prior_v = match in_chunk_prior.get(&state_key_hash) {
                Some(&v) => Some(v),
                None => self
                    .position_db
                    .find_prior_version(state_key_hash, version)
                    .map_err(|e| AptosDbError::Other(format!("find_prior_version: {e}")))?,
            };
            // Always emit a stale-index row — first writes use
            // `NO_PREV_VERSION` and the pruner skips them via
            // `is_first_write()`. Lets truncation iterate this CF
            // alone to reach every kv row.
            pos_batch
                .put::<StalePositionValueIndexSchema>(
                    &StalePositionValueIndex {
                        stale_since_version: version,
                        version: prior_v.unwrap_or(StalePositionValueIndex::NO_PREV_VERSION),
                        state_key_hash,
                    },
                    &(),
                )
                .map_err(|e| AptosDbError::Other(format!("stale_position_value_index put: {e}")))?;
            pos_batch
                .put::<PositionValueSchema>(&(state_key_hash, version), &maybe_value)
                .map_err(|e| {
                    AptosDbError::Other(format!("position_value batch put failed: {e}"))
                })?;

            in_chunk_prior.insert(state_key_hash, version);

            let value_hash = maybe_value.as_ref().map(StateValue::hash);
            position_merkle.push(MerkleLeafUpdate {
                state_key_hash,
                state_key: state_key.clone(),
                value_hash,
            });
        }

        Ok(NativeMerkleLeafUpdates {
            position: position_merkle,
        })
    }
```
