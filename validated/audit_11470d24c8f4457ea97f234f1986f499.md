# APTOS STATE-INTEGRITY REVIEW

### Title
Verified script cache in `UnsyncScriptCache`/`ScriptCache` is never invalidated when a dependency module is republished within the same execution session, causing stale linked scripts to execute against incompatible module bytecode - (File: `third_party/move/move-vm/types/src/code/cache/script_cache.rs`)

### Summary
The `ScriptCache` trait and its `UnsyncScriptCache`/`SyncScriptCache` implementations key cached scripts solely by the sha3-256 hash of the script's own bytes, with no dependency-version tracking. Once a script is inserted as `Code::Verified`, `insert_verified_script` refuses any further overwrite, and `unmetered_verify_and_cache_script` short-circuits to `return Ok(script)` on a cache hit without checking whether the modules it was linked against still match on-chain state. In the sequential block-executor path, the `UnsyncMap` (which owns the `UnsyncScriptCache`) is created once per block and shared across every transaction in that block, so a script verified early in the block against module `X` remains cached as `Verified` for the rest of the block even after a later transaction republishes/deletes `X`.

### Finding Description
`ScriptCache` is defined with no version/dependency tracking at all: [1](#0-0) 
compared to `ModuleCache`, which explicitly carries a `Version` associated type used for per-transaction read validation.

`insert_verified_script` never replaces an existing verified entry: [2](#0-1) 

`unmetered_verify_and_cache_script` returns the cached `Verified` script immediately on a hit, without re-checking `immediate_dependencies_iter()` against the current module cache state: [3](#0-2) 

In `execute_transactions_sequential`, a single `UnsyncMap` (which internally owns the script cache, see `UnsyncMap::script_cache`) is instantiated once for the whole block and reused across all transaction indices via `ViewState::Unsync(SequentialState::new(&unsync_map, ...))`: [4](#0-3) 

Module writes from each committed transaction are folded into the same `unsync_map.module_cache()` via `add_module_write_to_module_cache`, so republishing module `X` in transaction `N` correctly updates the *module* entry: [5](#0-4) 

However, nothing analogous exists for the script cache: `AptosCodeStorage`/`AptosCodeStorageAdapter` and `LatestView` both delegate `ScriptCache` straight through to the underlying `UnsyncScriptCache`/`SyncScriptCache` with no invalidation hook tied to module writes: [6](#0-5) [7](#0-6) 

Consequently, if transaction `N-1` in a block executes a script `S` (by hash) that was locally verified and linked against module `X`, and transaction `N` republishes `X` with an incompatible layout (e.g., changed struct fields, changed function signature, or removal of a function `S` calls), a later transaction `N+1` in the same block submitting the identical script bytes will hit `Some(Verified(script)) => return Ok(script)` and execute the already-linked `Script` object, which still references the old (now-inconsistent) module linkage baked in at verification time (function/struct handles resolved during `build_verified_script`). Execution proceeds using stale linkage rather than re-verifying against the new module.

### Impact Explanation
This breaks the required invariant that a `Verified` script's cached linkage must be invalidated when any of its dependency modules change. Because the module and script caches are decoupled, and only the module cache side has version tracking used in BlockSTM's read-set validation (`module_validation_v2`), a script re-execution against stale dependency bytecode can produce incorrect resource reads/writes (e.g., misinterpreting struct layout, calling a function that no longer exists in the way it was linked), yielding a write set that differs from the correct VM result for that transaction. This falls under the state-integrity gate: "Committed state that differs from the correct VM result."

### Likelihood Explanation
Reaching this requires wholly unprivileged actions: (1) submit a transaction that runs script `S` depending on module `X`, (2) submit a transaction from the module owner's account republishing `X` with an incompatible signature/layout change, (3) submit another transaction (same block) running the identical script bytes `S` again. All three steps are ordinary transaction/package-publish operations available to any account, and only require them to land in the same block under sequential execution (which is a normal BlockSTM fallback path, not an operator misconfiguration).

### Recommendation
Tie script cache entries to the module cache's versioning: either (a) add a `Version`/dependency-fingerprint to `ScriptCache::Verified` entries analogous to `ModuleCache::Version`, invalidating/re-verifying a cached verified script whenever any of its recorded immediate dependency modules are rewritten in the same session/block, or (b) flush/downgrade affected script cache entries back to `Deserialized` inside `add_module_write_to_module_cache` whenever a module write touches an address/name referenced by a cached verified script's dependency set.

### Proof of Concept
Integration-test sketch (mirrors the requested proof idea), to be run against `UnsyncCodeStorage`/`execute_transactions_sequential`:
1. Publish module `X` with function `f(): u64` used by script `S`.
2. Execute transaction A running script `S` (bytes hash `H`) — this inserts `Code::Verified` for `H` into the shared script cache, linked against `X` v1.
3. In the same block, execute transaction B republishing `X` with an incompatible change (e.g., `f` now takes an extra argument, or a struct field type used across the ABI changes).
4. Execute transaction C (same block) running script `S` (same bytes, same hash `H`) again.
5. Assert: `unmetered_verify_and_cache_script` for `H` in step 4 either re-verifies against `X` v2 or aborts the transaction — currently it returns the stale `Verified(script)` from step 2 unchanged via `Some(Verified(script)) => return Ok(script)`, demonstrating the missing invalidation.

Note: I was not able to fully trace whether `module_validation_v2` (parallel BlockSTM incarnation-finish validation) independently catches this scenario for the `SyncScriptCache`/parallel path within the available investigation; the sequential path (`execute_transactions_sequential` sharing one `UnsyncMap` per block) is confirmed by direct code reading above. Further tracing of `module_validation_v2`'s scope in `aptos-move/block-executor/src/executor.rs` would be needed to determine whether parallel execution has an independent mitigation that the sequential fallback lacks.

### Citations

**File:** third_party/move/move-vm/types/src/code/cache/script_cache.rs (L15-19)
```rust
pub trait ScriptCache {
    type Key: Eq + Hash + Clone;
    type Deserialized;
    type Verified;

```

**File:** third_party/move/move-vm/types/src/code/cache/script_cache.rs (L88-111)
```rust
    fn insert_verified_script(
        &self,
        key: Self::Key,
        verified_script: Self::Verified,
    ) -> Arc<Self::Verified> {
        use hashbrown::hash_map::Entry::*;

        match self.script_cache.borrow_mut().entry(key) {
            Occupied(mut entry) => {
                if !entry.get().is_verified() {
                    let new_script = Code::from_verified(verified_script);
                    let verified_script = new_script.verified().clone();
                    entry.insert(new_script);
                    verified_script
                } else {
                    entry.get().verified().clone()
                }
            },
            Vacant(entry) => entry
                .insert(Code::from_verified(verified_script))
                .verified()
                .clone(),
        }
    }
```

**File:** third_party/move/move-vm/runtime/src/storage/loader/eager.rs (L109-120)
```rust
    fn unmetered_verify_and_cache_script(&self, serialized_script: &[u8]) -> VMResult<Arc<Script>> {
        use Code::*;

        let hash = sha3_256(serialized_script);
        let deserialized_script = match self.module_storage.get_script(&hash) {
            Some(Verified(script)) => return Ok(script),
            Some(Deserialized(deserialized_script)) => deserialized_script,
            None => self
                .runtime_environment()
                .deserialize_into_script(serialized_script)
                .map(Arc::new)?,
        };
```

**File:** aptos-move/block-executor/src/executor.rs (L2067-2076)
```rust
        output_before_guard.for_each_module_write(&mut |module_id, state_value| {
            add_module_write_to_module_cache(
                module_id,
                state_value,
                txn_idx,
                runtime_environment,
                global_module_cache,
                unsync_map.module_cache(),
            )
        })?;
```

**File:** aptos-move/block-executor/src/executor.rs (L2150-2182)
```rust
        let unsync_map = UnsyncMap::new();

        let mut ret = Vec::with_capacity(num_txns + 1);

        let mut block_limit_processor = BlockGasLimitProcessor::<T>::new(
            self.config.onchain.block_gas_limit_type,
            self.config.onchain.block_gas_limit_override(),
            num_txns + 1,
        );

        let mut block_epilogue_txn = None;
        // Counts user-txn `accumulate_fee_statement` calls. Incremented alongside each
        // accumulate so any loop-exit path (including the bcs-fallback `continue`) keeps
        // this in sync. Passed as `num_committed` to `finish_*` so block-level counters
        // (`BLOCK_COMMITTED_TXNS`, `BLOCK_TXNS_CUT_BY_LIMIT`) report user-txn counts only.
        let mut num_committed_user_txns: u32 = 0;
        let mut idx = 0;
        while idx <= num_txns {
            let txn = if idx != num_txns {
                signature_verified_block.get_txn(idx as TxnIndex)
            } else if block_epilogue_txn.is_some() {
                block_epilogue_txn.as_ref().unwrap()
            } else {
                break;
            };
            let auxiliary_info = signature_verified_block.get_auxiliary_info(idx as TxnIndex);
            let latest_view = LatestView::<T, S>::new(
                base_view,
                module_cache_manager_guard.module_cache(),
                runtime_environment,
                ViewState::Unsync(SequentialState::new(&unsync_map, start_counter, &counter)),
                idx as TxnIndex,
            );
```

**File:** aptos-move/block-executor/src/code_cache.rs (L234-246)
```rust
#[delegate_to_methods]
#[delegate(ScriptCache, target_ref = "as_script_cache")]
impl<T: Transaction, S: TStateView<Key = T::Key>> LatestView<'_, T, S> {
    /// Returns the script cache.
    fn as_script_cache(
        &self,
    ) -> &dyn ScriptCache<Key = [u8; 32], Deserialized = CompiledScript, Verified = Script> {
        match &self.latest_view {
            ViewState::Sync(state) => state.versioned_map.script_cache(),
            ViewState::Unsync(state) => state.unsync_map.script_cache(),
        }
    }

```

**File:** third_party/move/move-vm/runtime/src/storage/implementations/unsync_code_storage.rs (L30-42)
```rust
/// Code storage that stores both modules and scripts (not thread-safe).
#[derive(Delegate)]
#[delegate(
    WithRuntimeEnvironment,
    target = "module_storage",
    where = "M: ModuleStorage"
)]
#[delegate(ModuleStorage, target = "module_storage", where = "M: ModuleStorage")]
#[delegate(ScriptCache, target = "script_cache", where = "M: ModuleStorage")]
pub struct UnsyncCodeStorage<M> {
    script_cache: UnsyncScriptCache<[u8; 32], CompiledScript, Script>,
    module_storage: M,
}
```
