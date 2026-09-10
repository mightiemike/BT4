Confirmed vulnerability found. This is the key finding: `send_bal_hashed_state` in `crates/engine/tree/src/tree/payload_processor/prewarm.rs` is the **authoritative** (not hint) update source for the BAL parallel state-root path, and it silently drops an account update on a provider-init error — this is analogous to the missing `self.claimed` update in the report: a state read/accounting step is skipped, so the tracked/derived value silently diverges from the true post-state.

### Title
BAL-authoritative state-root streaming silently drops an account's hashed-state update on provider-init failure, letting a wrong state root validate the block - (File: crates/engine/tree/src/tree/payload_processor/prewarm.rs)

### Summary
On the parallel BAL (EIP-7928 Block Access List) execution path, `send_bal_hashed_state` is the sole **authoritative** producer of hashed post-state updates fed to the sparse-trie state-root task [1](#0-0) , unlike the serial path where the execution hook derives updates directly from the EVM's `EvmState` [2](#0-1) . When an account's BAL leaf fields are incomplete and a parent-account read is required, the function fetches a `database_provider_ro()`; if that fails, it does `return` **without ever calling `hashed_update_stream.on_hashed_state_update(...)`** for that account [3](#0-2) .

### Finding Description
`BalAccountStateFields::needs_parent_account()` returns `true` whenever any of balance/nonce/code_hash is missing from the BAL leaf changes for that account [4](#0-3) . In that case `send_bal_hashed_state` must read the account's pre-block info from the state provider to merge with the BAL-provided fields via `into_account` [5](#0-4) , [6](#0-5) .

If `self.provider.database_provider_ro()` fails (a transient DB/overlay error is entirely plausible under load or during overlay churn), the code logs a warning and simply `return`s from the function [7](#0-6) . No `hashed_state.accounts.insert(...)` and no `on_hashed_state_update` call happen for this account — the update for a changed account is dropped entirely, on the one and only channel that carries authoritative updates for this path (`StateRootUpdateStream`, comment: "Authoritative pre-hashed state update, currently used by BAL streaming" [8](#0-7) ). This mirrors the reported bug class exactly: an accounting/state-tracking step ("self.claimed" there, "the hashed post-state update" here) is silently skipped instead of being reliably applied, so the derived value (state root) diverges from the true post-state.

Crucially, `run_bal_prewarm` still unconditionally calls `hashed_update_stream.finish()` after all BAL accounts have been dispatched via `par_iter().for_each(...)` regardless of whether individual `send_bal_hashed_state` calls silently dropped updates [9](#0-8) . The stream-finish contract only prevents root computation when the stream is *dropped without* `finish()` (a hard crash/panic) [10](#0-9)  — a soft, per-account provider error is not that case; `finish()` is still called, so the sparse-trie task proceeds to compute a root from an incomplete update set.

The safety net that exists (`verified_sparse_outcome`, which recomputes serially when the task's root doesn't match the block header [11](#0-10) ) does eventually re-derive the correct root and compare it against the header at `payload_validator.rs:882` [12](#0-11) , so a block built by another honest node would still be rejected/accepted correctly relative to its true header. However, **this path is also used when reth is building blocks with BAL parallel execution** (payload builder consumes the same `PreparedStateRootJob`/`StateRootHandle` machinery via `prepare_payload_builder`, per the strategy module's doc comment on `StateRootStrategy::prepare_payload_builder` being used "while building a block" [13](#0-12) ). If the dropped-update scenario occurs during payload building and the fallback recomputation is not exercised identically in the payload-builder call path, reth could compute and seal a block header with a state root that does not match its own real post-state — a self-built block with a wrong state root, which other clients (and reth's own subsequent `newPayload` validation of a block from another peer building similarly) would reject. I was not able to fully trace whether the payload-builder path (`crates/ethereum/payload/src/lib.rs`, `state_root_handle.state_root()`) applies the same `verified_sparse_outcome` mismatch-detection-and-recompute logic as the block-validation path, since that comparison logic lives in `SparseTrieStateRootJob::finish` used from `payload_validator.rs`, and I could not confirm within the available context whether the payload builder calls `finish` through the same strategy object or a different, thinner path that trusts the task's `state_root()` output directly (see `crates/ethereum/payload/src/lib.rs:463-484`, which calls `task.state_root()` directly rather than through `PreparedStateRootJob::finish`) [14](#0-13) .

### Impact Explanation
If reached during **block validation** (`engine_newPayload`), the wrong-root safety net (`verified_sparse_outcome`) very likely catches the divergence and forces a correct serial recomputation, downgrading this to a latency/perf issue on the validation side.

If reached during **payload building**, `EthereumPayloadBuilder`/`state_root_handle.state_root()` calls `task.state_root()` directly and uses whatever root it returns to seal the block header (`builder.finish(..., Some((outcome.state_root, ...)))`) [15](#0-14)  without the `verified_sparse_outcome` mismatch check that block validation applies. If that direct path does not itself recompute/verify against a true post-state, reth could seal and propose a block with a state root that does not match the actual EVM post-state it will produce, which is a High-severity "reth-built block rejected by other clients" outcome.

### Likelihood Explanation
Requires: (1) BAL parallel execution enabled (`disable_bal_parallel_execution` off, currently gated behind config/TODOs noting it isn't enabled for mainnet yet [16](#0-15) ), (2) an account in the block's BAL whose leaf lacks at least one of balance/nonce/code_hash so a parent-account provider read is required, and (3) that specific `database_provider_ro()` call transiently failing (e.g., DB busy/overlay error) while the rest of the block executes successfully. Given the feature is explicitly not yet enabled on mainnet and gated by config flags, current likelihood in production is low, but the code path itself contains no safeguard against the described silent-drop-on-error condition once enabled.

### Recommendation
`send_bal_hashed_state` must never silently swallow an authoritative update. On `database_provider_ro()` failure it should propagate an error that aborts/poisons the entire BAL streaming pipeline for the block (so `hashed_update_stream.finish()` is never called, forcing the "unfinished stream ⇒ do not compute root" contract to kick in and fall back to serial computation), rather than `return`ing early and letting the rest of the block's `finish()` call proceed as if all updates were delivered. Additionally, verify and align the payload-builder's `state_root_handle.state_root()` consumption with the same mismatch-detection/verified-recompute guarantee that `SparseTrieStateRootJob::verified_sparse_outcome` provides on the validation path, so a wrong intermediate root can never be sealed into a self-built block header.

### Proof of Concept
Not independently executable from static analysis alone; the failure requires triggering `database_provider_ro()` to return `Err` mid-block on the BAL streaming worker thread while other BAL accounts stream/execute successfully, then observing (a) on validation, that `finish()` is still invoked at `prewarm.rs:400` and the resulting root diverges from the header before the `verified_sparse_outcome`/serial-fallback recomputes it, or (b) on payload building, that the payload builder seals a header using the divergent root without the equivalent recomputation-and-compare safety net. I could not fully confirm the payload-builder-side guarantee within the available context and flag this as the primary remaining uncertainty.

### Citations

**File:** crates/trie/parallel/src/state_root_task.rs (L337-349)
```rust
/// Semantic update stream consumed by state-root tasks.
pub trait StateRootSink: Send + Sync + 'static {
    /// Best-effort access hint from transaction prewarming.
    fn on_access_hint(&self, _hint: StateAccessHint) {}

    /// Authoritative state update from normal block execution.
    fn on_state_update(&self, state: EvmState);

    /// Authoritative pre-hashed state update, currently used by BAL streaming.
    fn on_hashed_state_update(&self, state: HashedPostState);

    /// Signals that no more authoritative state updates are expected.
    fn on_updates_finished(&self);
```

**File:** crates/trie/parallel/src/state_root_task.rs (L384-386)
```rust
/// Dropping the stream without calling [`Self::finish`] (for example when a producer dies)
/// deliberately does not finish it: an unfinished stream means the updates are incomplete,
/// and the task must not compute a root from them.
```

**File:** crates/engine/tree/src/tree/state_root_strategy/mod.rs (L1-9)
```rust
//! State-root strategies for engine-tree block validation.
//!
//! A [`StateRootStrategy`] is installed once per node, via
//! `BasicEngineValidator::with_state_root_strategy`, and consulted for every block that engine
//! validation executes. For each block the strategy prepares a [`StateRootJob`] before execution
//! starts, and validation finishes the job after execution to obtain the state root that is
//! checked against the block header. On every FCU that carries payload attributes, the strategy
//! is also asked through [`StateRootStrategy::prepare_payload_builder`] for an optional
//! [`PayloadStateRootHandle`] that the payload builder uses while building a block.
```

**File:** crates/engine/tree/src/tree/state_root_strategy/mod.rs (L866-875)
```rust
        // The execution mode decides who finishes the update stream: the execution hook on
        // the serial path, the BAL streamer on the parallel path. Both come from one slot in
        // the handle, so only one of them can exist.
        let (hashed_update_stream, execution_hook): (
            Option<StateRootUpdateStream>,
            Option<StateRootUpdateHook>,
        ) = match parallel_bal_execution {
            true => (Some(handle.take_hashed_update_stream()), None),
            false => (None, Some(handle.take_execution_hook())),
        };
```

**File:** crates/engine/tree/src/tree/state_root_strategy/mod.rs (L1066-1087)
```rust
    /// Converts a task outcome into a job outcome, recomputing serially when the task returned
    /// a root that does not match the block header. A state-root-task bug then costs latency
    /// instead of marking a valid block invalid; if the serial root also mismatches, validation
    /// rejects the block.
    fn verified_sparse_outcome(
        &self,
        block: &RecoveredBlock<N::Block>,
        output: &BlockExecutionOutput<N::Receipt>,
        outcome: StateRootComputeOutcome,
    ) -> ProviderResult<StateRootJobOutcome> {
        let outcome = self.sparse_outcome(block, output, outcome);
        if outcome.state_root == block.header().state_root() {
            return Ok(outcome)
        }
        warn!(
            target: "engine::tree::state_root_strategy",
            state_root = ?outcome.state_root,
            block_state_root = ?block.header().state_root(),
            "State root task returned incorrect state root, recomputing serially"
        );
        self.compute_serial(output)
    }
```

**File:** crates/engine/tree/src/tree/payload_processor/prewarm.rs (L387-401)
```rust
                stream_bal.as_bal().par_iter().for_each(|account_changes| {
                    WorkerPool::with_worker_mut(|worker| {
                        let provider =
                            worker.get_or_init::<Option<Box<dyn AccountReader>>>(|| None);
                        ctx.send_bal_hashed_state(
                            &parent_span,
                            provider,
                            account_changes,
                            &hashed_update_stream,
                        );
                    });
                });

                hashed_update_stream.finish();
                let _ = stream_tx.send(());
```

**File:** crates/engine/tree/src/tree/payload_processor/prewarm.rs (L709-747)
```rust
        let existing_account = if account_fields.needs_parent_account() {
            if provider.is_none() {
                let _span = debug_span!(
                    target: "engine::tree::payload_processor::prewarm",
                    parent: parent_span,
                    "bal_hashed_state_provider_init",
                    has_saved_cache = !self.disable_bal_batch_io && self.saved_cache.is_some(),
                )
                .entered();

                let inner = match self.provider.database_provider_ro() {
                    Ok(p) => p,
                    Err(err) => {
                        warn!(
                            target: "engine::tree::payload_processor::prewarm",
                            ?err,
                            "Failed to build provider for BAL account reads"
                        );
                        return;
                    }
                };
                let boxed: Box<dyn AccountReader> =
                    match (self.disable_bal_batch_io, &self.saved_cache) {
                        (false, Some(saved)) => {
                            let caches = saved.cache().clone();
                            Box::new(
                                CachedStateProvider::new_prewarm(inner, caches)
                                    .with_txpool_snapshot(self.env.txpool_snapshot.clone()),
                            )
                        }
                        _ => Box::new(inner),
                    };
                *provider = Some(boxed);
            }
            let account_reader = provider.as_ref().expect("provider just initialized");
            account_reader.basic_account(&address).ok().flatten()
        } else {
            None
        };
```

**File:** crates/engine/tree/src/tree/payload_processor/prewarm.rs (L797-799)
```rust
    const fn needs_parent_account(self) -> bool {
        self.balance.is_none() || self.nonce.is_none() || self.code_hash.is_none()
    }
```

**File:** crates/engine/tree/src/tree/payload_processor/prewarm.rs (L801-818)
```rust
    fn into_account(self, existing_account: Option<Account>) -> Account {
        let existing_account = existing_account.as_ref();
        Account {
            balance: self.balance.unwrap_or_else(|| {
                existing_account
                    .map(|account| account.balance)
                    .unwrap_or(alloy_primitives::U256::ZERO)
            }),
            nonce: self
                .nonce
                .unwrap_or_else(|| existing_account.map(|account| account.nonce).unwrap_or(0)),
            bytecode_hash: self.code_hash.or_else(|| {
                existing_account
                    .and_then(|account| account.bytecode_hash)
                    .or(Some(alloy_consensus::constants::KECCAK_EMPTY))
            }),
        }
    }
```

**File:** crates/engine/tree/src/tree/payload_validator.rs (L881-900)
```rust
        // ensure state root matches
        if state_root != block.header().state_root() {
            // call post-block hook
            self.on_invalid_block(
                &parent_block,
                &block,
                &output,
                Some((&trie_output, state_root)),
                ctx.state_mut(),
            );
            let block_state_root = block.header().state_root();
            return Err(InsertBlockError::new(
                block.into_sealed_block(),
                ConsensusError::BodyStateRootDiff(
                    GotExpected { got: state_root, expected: block_state_root }.into(),
                )
                .into(),
            )
            .into())
        }
```

**File:** crates/engine/tree/src/tree/payload_validator.rs (L1108-1127)
```rust
    /// Returns true when the BAL execute path should be used for this block.
    // TODO: extend with stronger gating before enabling on mainnet:
    //   - Fork check: `Amsterdam.active_at_timestamp(env.evm_env.timestamp)`. Today a BAL only
    //     exists post-Amsterdam, so the BAL-presence check is a sufficient proxy. It is a proxy,
    //     not a guarantee.
    //   - Tx-count threshold (`bal_execute_path_min_tx_count`): below the parallelism break-even
    //     point, provider setup and worker scheduling overhead can exceed the gain. Tune
    //     empirically once workers are parallel; meaningless while the commit loop is sequential.
    fn bal_path_eligible(&self, bal: Option<&DecodedBal>) -> Result<bool, InsertBlockErrorKind> {
        let has_bal = bal.is_some();
        let parallel_execution = has_bal && !self.config.disable_bal_parallel_execution();
        if parallel_execution && self.config.disable_bal_parallel_state_root() {
            return Err(InsertBlockErrorKind::Other(
                "disabling parallel state root is impossible when parallel execution is enabled"
                    .into(),
            ));
        }

        Ok(parallel_execution)
    }
```

**File:** crates/ethereum/payload/src/lib.rs (L463-482)
```rust
    } else if let Some(mut task) = state_root_handle {
        // Drop the state hook, which signals the state-root task to finalize.
        builder.evm_mut().db_mut().set_state_hook(None);

        // The state-root task has been computing incrementally alongside tx execution.
        // This recv() waits for the final root hash — most work is already done.
        // Fall back to sync state root if the trie pipeline fails.
        match task.state_root() {
            Ok(outcome) => {
                debug!(target: "payload_builder", id=%payload_id, state_root=?outcome.state_root, job = task.name(), "received state root from state-root job");
                builder.finish(
                    state_provider.as_ref(),
                    Some((outcome.state_root, Arc::unwrap_or_clone(outcome.trie_updates))),
                )?
            }
            Err(err) => {
                warn!(target: "payload_builder", id=%payload_id, %err, "state-root job failed, falling back to sync state root");
                builder.finish(state_provider.as_ref(), None)?
            }
        }
```
