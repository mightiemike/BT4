## Finding

### Title
Stale `Verified` script served from shared `ScriptCache` without re-checking dependency validity - (File: `third_party/move/move-vm/runtime/src/storage/loader/eager.rs`)

### Summary
`EagerLoader::unmetered_verify_and_cache_script` short-circuits on a cache hit of an already-`Verified` script and returns it immediately, without re-checking that its `immediate_dependencies_iter()` modules are still the currently published/valid versions. The backing `ScriptCache` implementations (`UnsyncScriptCache`/`SyncScriptCache`) have no invalidation/override mechanism analogous to `GlobalModuleCache`'s `overridden` flag used for modules, so a script verified against one version of a dependency module remains permanently `Verified` in the cache even after that dependency is republished/upgraded within the same cache lifetime.

### Finding Description
`unmetered_verify_and_cache_script` in `EagerLoader`: [1](#0-0) 
returns the cached `Verified` script directly on a hit, with no call to `immediate_dependencies_iter()` and no re-fetch of `unmetered_get_existing_eagerly_verified_module` for its dependencies — unlike the initial verification path a few lines below that does perform this check.

Contrast this with `LazyLoader::metered_verify_and_cache_script`, whose cache-hit path at least walks `immediate_dependencies_iter()` and calls `charge_module` for each dependency: [2](#0-1) 
Even there, `charge_module` only charges gas by size via `unmetered_get_existing_module_size`; it does not re-verify or reject stale scripts if the module has been overridden by a republish.

The `ScriptCache` trait/implementations have no concept of invalidation: [3](#0-2) 
Once an entry is `Verified`, `insert_verified_script` will never replace it with a fresh verification result: [4](#0-3) 

This is unlike `GlobalModuleCache`, which explicitly supports `mark_overridden`/`contains_not_overridden` for modules to force re-fetch after republishing: [5](#0-4) 

In the block executor, the script cache used during a block's execution is a single `SyncScriptCache` embedded in `MVHashMap`, shared by all transactions in that block via `LatestView`: [6](#0-5) [7](#0-6) 
This matches the premise in the question: multiple `EagerLoader::new(module_storage)` constructions across different transactions in a block share the same underlying `ScriptCache`.

Critically, unlike module reads (which are captured into `captured_reads` and validated for Block-STM conflict detection via `validate_module_reads`, as seen in the module test): [8](#0-7) 
there is no equivalent script/script-dependency read capture or validation path found for the `ScriptCache` fast-hit path. On a `Verified` cache hit in `eager.rs`, no read of the dependency module's state key is registered at all, so Block-STM's conflict-detection machinery (which relies on read-set vs. write-set intersection) has nothing to invalidate against when the dependency module is later republished by another transaction in the same block.

### Impact Explanation
If a script is verified and cached against module `M`, and a subsequent transaction in the same block republishes/upgrades `M` (incompatibly, e.g., removing/renaming a function or changing a struct layout), a later transaction invoking the same script hash will:
1. Hit the `Some(Verified(script)) => return Ok(script)` fast path in `eager.rs`, bypassing `immediate_dependencies_iter()` re-verification entirely.
2. Execute against the stale `Verified` script's baked-in linkage to the old `M`, producing a write set computed from now-invalid module code/layout.
3. Not register any dependency/module read that Block-STM could use to invalidate and force re-execution, since the entire dependency-check code path is skipped on the fast hit.

This can commit a write set inconsistent with the currently published module set — a state-integrity violation matching the "Impact" criteria (corrupting committed state via a wrong write set from stale linked code).

### Likelihood Explanation
Exploitability depends on: (a) whether `EagerLoader` (used when lazy loading is disabled, per `is_lazy_loading_enabled()` returning `false`) is actually reachable on the mainnet execution path for a given `gas_feature_version`/config, and (b) whether the block executor's `SyncScriptCache` instance genuinely persists cache hits across transactions within a single block without any interstitial revalidation elsewhere in the pipeline that I could not fully trace (e.g., a coarser-grained flush on any module publish, or overall re-execution triggered by other read/write conflicts on the publishing transaction's other side effects). I could not find, within the indexed content, an explicit mechanism that flushes or invalidates the script cache when a module is republished mid-block, nor a captured-read entry for script dependency modules on the fast-hit path — but I also could not conclusively rule out an external invalidation guard (e.g., publish/republish transactions being serialized to run last, or Block-STM detecting the conflict through other means such as the publishing transaction's own resource writes). This limits confidence to "credible, not fully confirmed."

### Recommendation
- On a `Verified` script cache hit in `eager.rs`'s `unmetered_verify_and_cache_script`, re-validate (or at minimum re-register a dependency read of) each entry from `immediate_dependencies_iter()` against the current `module_storage`, mirroring what `check_dependencies_and_charge_gas` does for the deserialized-script path.
- Add an override/invalidation mechanism to `ScriptCache` (analogous to `GlobalModuleCache::mark_overridden`) so that when a module is republished, any cached `Verified` script depending on it is invalidated and forces re-verification.
- Ensure Block-STM's `captured_reads`/validation path also tracks script-dependency module reads so that republishing a dependency module correctly triggers re-execution of transactions that consumed a cached verified script referencing it.

### Proof of Concept
Conceptual reproduction (would need to be executed against the actual block executor / e2e test harness to confirm):
1. Publish module `M` with function `f`.
2. Submit transaction `Tx1` executing script `S` whose `immediate_dependencies_iter()` includes `M::f`; this triggers `unmetered_verify_and_cache_script`, inserting a `Verified` entry for `S` keyed by its sha3-256 hash into the shared `SyncScriptCache`.
3. Within the same block, submit `Tx2` that republishes `M` in an incompatible way (e.g., removes/renames `f`) — this marks `M`'s entry in `GlobalModuleCache` as `overridden`.
4. Submit `Tx3` (same block, after `Tx2`) executing the same script `S` (identical bytes/hash). Instrument `eager.rs` to assert that `unmetered_get_existing_eagerly_verified_module` is invoked for `M` before returning the script from cache. Expectation of the report: `Tx3` should re-verify and fail (or resolve against the new `M`), but instead hits `Some(Verified(script)) => return Ok(script)` and executes with stale linkage, without any assertion failure or re-verification call.

Given the reliance on internal invariants I could not fully verify end-to-end in this review pass (particularly cross-transaction Block-STM interaction with republish and any external validation nets), this should be treated as a high-priority candidate for confirmation via an actual runnable reproduction in the aptos-core test harness (e.g. `e2e-move-tests`) rather than a fully proven exploit from static review alone.

### Citations

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

**File:** third_party/move/move-vm/runtime/src/storage/loader/lazy.rs (L130-142)
```rust

        let hash = sha3_256(serialized_script);
        let deserialized_script = match self.module_storage.get_script(&hash) {
            Some(Verified(script)) => {
                // Before returning early, meter modules because script might have been cached by
                // other thread.
                for (addr, name) in script.immediate_dependencies_iter() {
                    let module_id = ModuleId::new(*addr, name.to_owned());
                    self.charge_module(gas_meter, traversal_context, &module_id)
                        .map_err(|err| err.finish(Location::Undefined))?;
                }
                return Ok(script);
            },
```

**File:** third_party/move/move-vm/types/src/code/cache/script_cache.rs (L13-43)
```rust
/// Interface used by any script cache implementation.
#[delegatable_trait]
pub trait ScriptCache {
    type Key: Eq + Hash + Clone;
    type Deserialized;
    type Verified;

    /// If the entry associated with the key is vacant, inserts the script and returns its copy.
    /// Otherwise, there is no insertion and the copy of existing entry is returned.
    fn insert_deserialized_script(
        &self,
        key: Self::Key,
        deserialized_script: Self::Deserialized,
    ) -> Arc<Self::Deserialized>;

    /// If the entry associated with the key is vacant, inserts the script and returns its copy.
    /// If the entry associated with the key is occupied, but the entry is not verified, inserts
    /// the script returning the copy. Otherwise, there is no insertion and the copy of existing
    /// (verified) entry is returned.
    fn insert_verified_script(
        &self,
        key: Self::Key,
        verified_script: Self::Verified,
    ) -> Arc<Self::Verified>;

    /// Returns the script if it has been cached before, or [None] otherwise.
    fn get_script(&self, key: &Self::Key) -> Option<Code<Self::Deserialized, Self::Verified>>;

    /// Returns the number of scripts stored in cache.
    fn num_scripts(&self) -> usize;
}
```

**File:** third_party/move/move-vm/types/src/code/cache/script_cache.rs (L167-190)
```rust
    fn insert_verified_script(
        &self,
        key: Self::Key,
        verified_script: Self::Verified,
    ) -> Arc<Self::Verified> {
        use dashmap::mapref::entry::Entry::*;

        match self.script_cache.entry(key) {
            Occupied(mut entry) => {
                if !entry.get().is_verified() {
                    let new_script = Code::from_verified(verified_script);
                    let verified_script = new_script.verified().clone();
                    entry.insert(CachePadded::new(new_script));
                    verified_script
                } else {
                    entry.get().verified().clone()
                }
            },
            Vacant(entry) => entry
                .insert(CachePadded::new(Code::from_verified(verified_script)))
                .verified()
                .clone(),
        }
    }
```

**File:** aptos-move/block-executor/src/code_cache_global.rs (L112-139)
```rust
    /// Returns true if the key exists in cache and the corresponding module is not overridden.
    pub fn contains_not_overridden(&self, key: &K) -> bool {
        self.module_cache
            .get(key)
            .is_some_and(|entry| entry.is_not_overridden())
    }

    /// Marks the cached module (if it exists) as overridden. As a result, all subsequent calls to
    /// the cache for the associated key will result in a cache miss. If an entry does not exist,
    /// it is a no-op.
    pub fn mark_overridden(&self, key: &K) {
        if let Some(entry) = self.module_cache.get(key) {
            entry.mark_overridden();
        }
    }

    /// Returns the module stored in cache. If the module has not been cached, or it exists but is
    /// overridden, [None] is returned.
    pub fn get<Q>(&self, key: &Q) -> Option<Arc<ModuleCode<D, V, E>>>
    where
        Q: Hash + Equivalent<K>,
    {
        self.module_cache.get(key).and_then(|entry| {
            entry
                .is_not_overridden()
                .then(|| Arc::clone(entry.module_code()))
        })
    }
```

**File:** aptos-move/mvhashmap/src/lib.rs (L39-47)
```rust
pub struct MVHashMap<K, T, V, I> {
    data: VersionedData<K, V>,
    group_data: VersionedGroupData<K, T, V>,
    delayed_fields: VersionedDelayedFields<I>,

    module_cache:
        SyncModuleCache<ModuleId, CompiledModule, Module, AptosModuleExtension, Option<TxnIndex>>,
    script_cache: SyncScriptCache<[u8; 32], CompiledScript, Script>,
}
```

**File:** aptos-move/mvhashmap/src/lib.rs (L116-119)
```rust
    /// Returns the script cache.
    pub fn script_cache(&self) -> &SyncScriptCache<[u8; 32], CompiledScript, Script> {
        &self.script_cache
    }
```

**File:** aptos-move/block-executor/src/captured_reads.rs (L2304-2348)
```rust
    #[test]
    fn test_global_and_block_cache_module_reads() {
        let mut captured_reads = test_captured_reads!(new);
        let mut global_module_cache = GlobalModuleCache::empty();
        let per_block_module_cache = SyncModuleCache::empty();

        // Module exists in global cache.
        let m = mock_verified_code(0, MockExtension::new(8));
        global_module_cache.insert(0, m.clone());
        captured_reads.capture_global_cache_read(0, m);
        assert!(captured_reads.validate_module_reads(
            &global_module_cache,
            &per_block_module_cache,
            None
        ));

        // Assume we republish this module: validation must fail.
        let a = mock_deserialized_code(100, MockExtension::new(8));
        global_module_cache.mark_overridden(&0);
        per_block_module_cache
            .insert_deserialized_module(
                0,
                a.code().deserialized().as_ref().clone(),
                a.extension().clone(),
                Some(10),
            )
            .unwrap();

        let valid = captured_reads.validate_module_reads(
            &global_module_cache,
            &per_block_module_cache,
            None,
        );
        assert!(!valid);

        // Assume we re-read the new correct version. Then validation should pass again.
        captured_reads.capture_per_block_cache_read(0, Some((a, Some(10))));
        assert!(captured_reads.validate_module_reads(
            &global_module_cache,
            &per_block_module_cache,
            None
        ));
        assert!(!global_module_cache.contains_not_overridden(&0));
    }

```
