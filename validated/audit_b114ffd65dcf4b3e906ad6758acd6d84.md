This confirms the critical finding: on the parallel BAL execution path, `hashed_update_stream` is documented as the **authoritative** update capability, not a best-effort hint — it is the sole source that determines `final_hashed_state` and therefore the computed state root, as opposed to the actual per-transaction results produced by `execute_block`/`canonical_state` in `crates/engine/tree/src/tree/payload_processor/bal/execute.rs`.

### Title
BAL-derived speculative account reconstruction used as authoritative state-root input can diverge from the real post-execution account state - (File: crates/engine/tree/src/tree/payload_processor/prewarm.rs)

### Summary
On the parallel BAL execution path, block validation does not feed the state-root pipeline from the real, committed `BundleState` produced by executing the block (as the serial path does via the execution hook). Instead it feeds it from `send_bal_hashed_state`, which reconstructs each account's `balance`/`nonce`/`bytecode_hash` from the *received* BAL's last-recorded changes (`BalAccountStateFields::from_changes`), falling back to a `basic_account` read of the *parent* state for any field the BAL didn't explicitly record (`BalAccountStateFields::into_account`). This reconstructed, speculative account is sent via `StateRootUpdateStream::on_hashed_state_update`, which `sparse_trie.rs` treats as authoritative (`StateRootSink::on_hashed_state_update` doc: "Authoritative pre-hashed state update, currently used by BAL streaming"). [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
The equality that must hold is: *the state root committed by reth for a BAL-executed block must equal the root of the state actually produced by the real, authoritative execution of the block's transactions (the `BundleState` from `execute_block` in `bal/execute.rs`)*. This mirrors the reported bug class: a value (BTC/USD) is substituted for the "real" quantity (WBTC/USD) under an assumption that the two are pegged/equal, and the substitution is trusted as authoritative rather than cross-checked against the ground truth.

Here, `BalAccountStateFields::into_account` in `prewarm.rs` substitutes:
- The BAL's `balance_changes.last()/nonce_changes.last()/code_changes.last()` for the "real" post-execution field, and
- The **parent block's** committed account (`existing_account`, read via `AccountReader::basic_account` against parent state) for any field the BAL omitted. [4](#0-3) 

This is only correct if the submitted BAL is complete and accurate for every field of every touched account. The actual authoritative execution (`bal/execute.rs::execute_block_inner`) re-executes transactions with real EVM semantics and commits the *actual* resulting account via `commit_transaction`, then separately rebuilds its own BAL and only *logs* (at `debug` level) a divergence between the received and rebuilt BAL — it does not reject the block or otherwise reconcile the hashed-state stream that already went to the state-root task: [5](#0-4) 

Because `send_bal_hashed_state` is what's wired as the authoritative `StateRootUpdateStream` for the BAL path (`state_root_strategy/mod.rs` explicitly documents that on the parallel path "the authoritative capability went to the hashed update stream instead" of the execution hook that carries real `EvmState`), the sparse trie's `final_hashed_state` is built entirely from BAL-derived reconstructions, not from `canonical_executor`'s real committed results: [6](#0-5) [7](#0-6) 

If the submitted BAL under-reports an account's touched fields in a way that doesn't match what full serial semantics would produce (e.g., a field the block producer's BAL omitted because it assumed a no-op, or the BAL was crafted by a malicious/buggy block-builder for a self-built payload), the reconstructed account fed to the trie can differ from what `execute_block`'s canonical, real EVM execution actually commits to `BundleState`. There is no code path shown that re-derives the final hashed state from the real `BundleState` and compares/overwrites it against the BAL-derived one before the state root is finalized — the divergence check in `take_built_bal_and_log_divergence` only compares BAL *bytes* for logging, gated behind `debug` tracing being enabled, and never invalidates the block or corrects the already-streamed state-root updates.

### Impact Explanation
If the BAL-derived hashed state diverges from the real post-execution state and this divergence is not caught, reth would compute and commit a **wrong state root** for the block on the BAL/parallel execution path — a state root that differs from what a full spec-compliant re-execution would produce. This falls squarely in the Critical category ("a state root...differing from the spec") since the state-root task's output is what's compared against the block header's `state-root` field for consensus validity. This also risks a consensus split: nodes running the BAL-parallel path would compute a different root than nodes doing pure serial execution, or than the block's true canonical root, potentially causing the node to either accept an invalid block or reject/mark-invalid a genuinely valid one depending on which side of the divergence the header's committed root falls on.

### Likelihood Explanation
This requires that the block came with a BAL (EIP-7928, `parallel_bal_execution = true`) and that a divergence actually occurs between BAL contents and real serial semantics — the debug-log-only divergence check (`take_built_bal_and_log_divergence`) implies divergences are anticipated/possible in practice, not purely theoretical, since the authors added detection logic for it. However, I could not fully confirm within the available context whether there is a *separate, later reconciliation step* (e.g., in `payload_validator.rs` after `finish()`/`take_hashed_state_rx()`) that re-verifies or overrides the sparse trie's `final_hashed_state` against the real `BundleState` before the state root is treated as final. The `PreparedStateRootJob::finish` method receives the real `BlockExecutionOutput` and a `LazyHashedPostState` — it's plausible this step performs a reconciliation/consistency check that would catch or prevent the divergence from being finalized as the state root. This uncertainty means the exact severity/exploitability depends on code outside what was retrieved; a full audit would need to trace `SparseTrieStateRootJob::finish` and `LazyHashedPostState` construction to confirm whether the BAL-derived stream is ever cross-checked against the real bundle state, or whether it is trusted outright.

### Recommendation
Confirm (via `SparseTrieStateRootJob::finish` / `LazyHashedPostState`, not shown here) that the final state root computed from the BAL-derived `hashed_update_stream` is validated or reconciled against the real `BundleState` produced by `execute_block`'s canonical execution before being accepted as the block's state root. If no such reconciliation exists, either (a) always cross-check `built_bal` against `input_bal` and reject the block (or fully recompute the state root from `BundleState`) on any divergence rather than only logging it under `debug` tracing, or (b) derive the authoritative hashed-state stream from the real committed `BundleState` rather than from `BalAccountStateFields::into_account`'s speculative reconstruction.

### Proof of Concept
Not independently verified end-to-end (would require constructing a BAL/payload pair where the submitted BAL's `balance_changes`/`nonce_changes`/`code_changes` for some account omit a field that differs between "parent value" and the account's true post-execution value, then observing whether the resulting state root differs from a full serial re-execution). Conceptually:
1. Build a payload with a BAL where an account's field (e.g., nonce) is *not recorded* in `nonce_changes`, so `BalAccountStateFields::into_account` falls back to the parent account's nonce via `basic_account`.
2. Craft a transaction that actually *does* change that account's nonce during real serial execution (e.g., because the BAL omitted a nonce bump the producer's builder didn't record, or recorded a stale value).
3. `send_bal_hashed_state` streams the wrong (parent-value) nonce to the state-root task as "authoritative."
4. `execute_block_inner`'s canonical execution commits the correct nonce to `BundleState`, and diverges from the BAL only logged at `debug` level via `take_built_bal_and_log_divergence`.
5. If no downstream reconciliation exists (unconfirmed), the final state root is computed from the wrong nonce, producing a state root divergent from the spec-correct one.

### Citations

**File:** crates/engine/tree/src/tree/payload_processor/prewarm.rs (L709-750)
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

        let account = account_fields.into_account(existing_account);
        let hashed_address = hashed_address.unwrap_or_else(|| keccak256(address));
```

**File:** crates/engine/tree/src/tree/payload_processor/prewarm.rs (L778-818)
```rust
impl BalAccountStateFields {
    fn from_changes(account_changes: &alloy_eip7928::AccountChanges) -> Self {
        Self {
            balance: account_changes.balance_changes.last().map(|change| change.post_balance),
            nonce: account_changes.nonce_changes.last().map(|change| change.new_nonce),
            code_hash: account_changes.code_changes.last().map(|code_change| {
                if code_change.new_code.is_empty() {
                    alloy_consensus::constants::KECCAK_EMPTY
                } else {
                    keccak256(&code_change.new_code)
                }
            }),
        }
    }

    const fn is_empty(self) -> bool {
        self.balance.is_none() && self.nonce.is_none() && self.code_hash.is_none()
    }

    const fn needs_parent_account(self) -> bool {
        self.balance.is_none() || self.nonce.is_none() || self.code_hash.is_none()
    }

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

**File:** crates/trie/parallel/src/state_root_task.rs (L345-349)
```rust
    /// Authoritative pre-hashed state update, currently used by BAL streaming.
    fn on_hashed_state_update(&self, state: HashedPostState);

    /// Signals that no more authoritative state updates are expected.
    fn on_updates_finished(&self);
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

**File:** crates/engine/tree/src/tree/payload_processor/bal/execute.rs (L202-226)
```rust
fn take_built_bal_and_log_divergence<DB>(
    canonical_state: &mut State<DB>,
    received_bal: &AlloyBal,
) -> BlockAccessList
where
    DB: Database,
{
    let built_bal = canonical_state.take_built_alloy_bal().expect("with_bal_builder set");
    if tracing::enabled!(target: "engine::tree::payload_processor::bal", tracing::Level::DEBUG) &&
        built_bal.as_slice() != received_bal.as_slice()
    {
        let rebuilt = compute_block_access_list_hash(built_bal.as_slice());
        let expected = compute_block_access_list_hash(received_bal.as_slice());
        let div = received_bal.diff(built_bal.as_slice());
        tracing::debug!(
            target: "engine::tree::payload_processor::bal",
            %rebuilt,
            %expected,
            %div,
            "first BAL divergence",
        );
    }

    built_bal
}
```

**File:** crates/engine/tree/src/tree/payload_validator.rs (L633-639)
```rust
        // The hook exists only when `prepare` installed it (serial path); on the parallel BAL
        // path the authoritative capability went to the hashed update stream instead.
        let execution_state_hook = state_root_job.take_execution_hook();
        // The prewarm capabilities go to the code that produces their messages and are not
        // retained anywhere else, so the task's update channel closes when producers finish.
        let hint_stream = state_root_job.take_hint_stream();
        let hashed_update_stream = state_root_job.take_hashed_update_stream();
```

**File:** crates/engine/tree/src/tree/state_root_strategy/sparse_trie.rs (L495-544)
```rust
    /// Processes a hashed state update and encodes all state changes as trie updates.
    #[instrument(
        level = "trace",
        target = "engine::tree::payload_processor::sparse_trie",
        skip_all
    )]
    fn on_hashed_state_update(&mut self, hashed_state_update: HashedPostState) {
        for (&address, storage) in &hashed_state_update.storages {
            if !storage.storage.is_empty() {
                // Look up outer maps once per address instead of once per slot.
                let new_updates = self.new_storage_updates.entry(address).or_default();
                let mut existing_updates = self.storage_updates.get_mut(&address);

                for (&slot, &value) in &storage.storage {
                    let encoded = if value.is_zero() {
                        Vec::new()
                    } else {
                        alloy_rlp::encode_fixed_size(&value).to_vec()
                    };
                    new_updates.insert(slot, LeafUpdate::Changed(encoded));

                    // Remove an existing storage update if it exists.
                    if let Some(ref mut existing) = existing_updates {
                        existing.remove(&slot);
                    }
                }
            }

            // Make sure account is tracked in `account_updates` so that it is revealed in accounts
            // trie for storage root update.
            self.new_account_updates.entry(address).or_insert(LeafUpdate::Touched);

            // Make sure account is tracked in `pending_account_updates` so that once storage root
            // is computed, it will be updated in the accounts trie.
            self.pending_account_updates.entry(address).or_insert(None);
        }

        for (&address, &account) in &hashed_state_update.accounts {
            // Track account as touched.
            //
            // This might overwrite an existing update, which is fine, because storage root from it
            // is already tracked in the trie and can be easily fetched again.
            self.new_account_updates.insert(address, LeafUpdate::Touched);

            // Track account in `pending_account_updates` so that once storage root is computed,
            // it will be updated in the accounts trie.
            self.pending_account_updates.insert(address, Some(account));
        }

        self.final_hashed_state.extend(hashed_state_update);
```
