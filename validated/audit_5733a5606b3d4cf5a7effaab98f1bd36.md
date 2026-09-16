### Title
Stale/Unverified Cairo0 Class Cache Entries Bypass Declaration Check in `get_compiled_from_class_manager` - ([File: crates/blockifier/src/state/state_reader_and_contract_manager.rs])

### Summary
`StateReaderAndContractManager::get_compiled_from_class_manager` explicitly re-validates a cached Cairo1 class against the canonical state (`is_declared`) before serving it, because the comment in the code itself acknowledges the cache "might contain a declared class from a reverted block." However, that verification is skipped entirely for Cairo0 (`RunnableCompiledClass::V0`) classes: [1](#0-0) 

This mirrors the structural pattern in the reported Xen CVE: a performance cache (EPT cache / here, the process-global `ContractClassManager`/`GlobalContractCache`) is not kept strictly in sync with the authoritative structures it caches (page tables / here, the on-disk declared-classes state), and a code path trusts the stale cached entry without re-validating it, allowing access to something ("memory not owned by the guest" / here, "code for a class hash that is not currently declared").

### Finding Description
The `ContractClassManager` (`RawClassCache`, a `GlobalContractCache`) is a process-wide cache shared across all blocks and all speculative/validate-only executions handled by a sequencer node: [2](#0-1) [3](#0-2) 

Entries are inserted into this cache via `set_and_compile`/`class_cache.set` whenever `get_compiled_classes` is invoked, i.e., whenever *any* execution attempt (including speculative execution of a proposal that is later discarded, aborted, or reverted, or validate-only/estimate-fee flows) reads a class: [4](#0-3) 

For Cairo1 classes, `get_compiled_from_class_manager` protects against staleness (e.g. a class hash whose DECLARE was later reverted or that was only speculatively present in a discarded proposal) by calling `self.state_reader.is_declared(class_hash)` against the canonical, committed state before trusting the cache hit. For Cairo0 (V0) classes, this branch is a no-op:
```rust
match &runnable_class {
    RunnableCompiledClass::V0(_) => {}
    _ => { /* is_declared check */ }
}
```
The `is_declared` trait doc even documents this gap: "Cairo 0 classes always return `false`" for `is_declared`, meaning the underlying state-reader implementation cannot even distinguish "declared V0 class" from "not declared" for verification purposes — so the check is bypassed by design rather than fixed. [5](#0-4) 

No cache-clearing call tied to block revert was found for this cache in the reachable code (`GlobalContractCache::clear` / `ContractClassManager::clear` exist but are not observed being wired into the state-diff revert path): [6](#0-5) [7](#0-6) 

### Impact Explanation
If a Cairo0 class hash is ever populated into this global cache by any execution path that does not correspond to a permanently-committed DECLARE (e.g., speculative/validate-only execution of a proposal that is later discarded by a validator, or a block that is later reverted), that class remains servable from the cache indefinitely with no re-check against the canonical declared-classes table. A subsequent legitimate transaction (`deploy`, `deploy_account`, `library_call`, `replace_class`) referencing that same class hash on a node whose cache happens to hold the stale entry will succeed and execute the class's code, whereas an honest node without that cache poisoning (e.g., one that never processed the discarded/reverted proposal) would correctly reject it via `UndeclaredClassHash`. This produces execution and state-root divergence between nodes for the same input transaction — inconsistent state commitments and potential inability of the network to agree on the resulting block, matching "honest-node divergence" / "wrong committed root."

### Likelihood Explanation
Reaching this requires: (1) getting a Cairo0 class hash speculatively cached by the process without it becoming a permanently committed DECLARE (achievable by a proposer submitting a proposal containing the DECLARE that is subsequently rejected/reverted, or via validate-only/simulate flows that call into `get_compiled_class`), and (2) a subsequent transaction from any sender referencing that class hash via `deploy`/`replace_class`/`library_call`. Both steps are reachable purely through submitted transactions/declared classes, without any operator or node privilege — matching the required reachable-path constraint. The main uncertainty is how consistently the caching global class hash survives across proposal rejection/reverts in the current deployment topology (batcher vs validator vs gateway processes), which could not be fully confirmed with the available tools.

### Recommendation
Apply the same `is_declared`-style verification to Cairo0/V0 classes as is done for Cairo1, using a declared-classes/deployed-contracts check that actually supports V0 classes (rather than the current `is_declared` stub that always returns `false` for V0). Additionally, ensure the global class cache is explicitly invalidated/scoped per canonical, committed state (e.g., cleared or entry-versioned on block revert, and not populated from speculative/validate-only or ultimately-discarded proposal execution) so it cannot outlive the state it was derived from.

### Proof of Concept
Conceptual reproduction (based on code inspection; could not be dynamically executed):
1. A proposer submits a proposal that includes a `DECLARE` of a Cairo0 class `C` and an `INVOKE`/other tx referencing it, causing the validating node to execute `get_compiled_class(C)` (via `get_compiled_from_class_manager`), which calls `set_and_compile`, inserting `C` into the process-global `ContractClassManager` cache.
2. The proposal is discarded (fails consensus, is superseded, or is later reverted), so `C` is never actually committed as declared in canonical state — but the class-cache entry for `C` persists (no revert-triggered `clear()` was found wired to this cache).
3. A later, unrelated, legitimate transaction submitted by any sender performs `deploy_syscall(class_hash=C, ...)` (crates/blockifier/src/execution/syscalls/syscall_base.rs `deploy`), which eventually calls `state.get_compiled_class(C)`.
4. Because `C` resolves to `RunnableCompiledClass::V0`, `get_compiled_from_class_manager` skips the `is_declared` check entirely and returns the cached class, allowing the deployment/execution to proceed even though `C` is not declared in the canonical state — producing a result that diverges from a node that never cached `C`.

### Citations

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L16-22)
```rust
pub trait FetchCompiledClasses: StateReader {
    fn get_compiled_classes(&self, class_hash: ClassHash) -> StateResult<CompiledClasses>;

    /// Returns whether the given class hash corresponds to a declared Cairo 1 class.
    /// Cairo 0 classes always return `false`.
    fn is_declared(&self, class_hash: ClassHash) -> StateResult<bool>;
}
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L70-87)
```rust
        if let Some(runnable_class) =
            self.contract_class_manager.get_runnable(&class_hash, &self.native_classes_whitelist)
        {
            match &runnable_class {
                RunnableCompiledClass::V0(_) => {}
                _ => {
                    // The Cairo1 class is cached; verify it is declared,
                    // since existence in the cache does not guarantee that
                    // (it might contain a declared class from a reverted block, for example).
                    if !self.state_reader.is_declared(class_hash)? {
                        return Err(StateError::UndeclaredClassHash(class_hash));
                    }
                }
            }
            self.increment_cache_hit_metric();
            self.update_native_metrics(&runnable_class);
            return Ok(runnable_class);
        }
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L88-104)
```rust
        self.increment_cache_miss_metric();

        let compiled_class = self.state_reader.get_compiled_classes(class_hash)?;
        self.contract_class_manager.set_and_compile(class_hash, compiled_class.clone());
        // Access the cache again in case the class was compiled.
        let runnable_class = self
            .contract_class_manager
            .get_runnable(&class_hash, &self.native_classes_whitelist)
            .unwrap_or_else(|| {
                // Edge case that should not be happen if the cache size is big enough.
                // TODO(Yoni): consider having an atomic set-and-get.
                log::error!("Class is missing immediately after being cached.");
                compiled_class.to_runnable()
            });
        self.update_native_metrics(&runnable_class);
        Ok(runnable_class)
    }
```

**File:** crates/blockifier/src/state/global_cache.rs (L64-64)
```rust
pub type RawClassCache = GlobalContractCache<CompiledClasses>;
```

**File:** crates/starknet_api/src/class_cache.rs (L16-29)
```rust
impl<T: Clone> GlobalContractCache<T> {
    /// Locks the cache for atomic access. Although conceptually shared, writing to this cache is
    /// only possible for one writer at a time.
    pub fn lock(&self) -> LockedClassCache<'_, T> {
        self.0.lock().expect("Global contract cache is poisoned.")
    }

    pub fn get(&self, class_hash: &ClassHash) -> Option<T> {
        self.lock().cache_get(class_hash).cloned()
    }

    pub fn set(&self, class_hash: ClassHash, contract_class: T) {
        self.lock().cache_set(class_hash, contract_class);
    }
```

**File:** crates/starknet_api/src/class_cache.rs (L31-33)
```rust
    pub fn clear(&mut self) {
        self.lock().cache_clear();
    }
```

**File:** crates/blockifier/src/state/native_class_manager.rs (L229-231)
```rust
    pub fn clear(&mut self) {
        self.class_cache.clear();
    }
```
