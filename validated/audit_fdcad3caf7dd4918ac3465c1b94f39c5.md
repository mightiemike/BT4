## Finding: Stale Global Class Cache Allows Undeclared Cairo0 Classes to Be Treated as Declared

### Title
Undeclared Cairo0 (V0) contract classes can be executed via a stale global class cache after a discarded/reverted speculative declaration - (File: crates/blockifier/src/state/state_reader_and_contract_manager.rs)

### Summary
`StateReaderAndContractManager::get_compiled_from_class_manager` re-verifies against the canonical state (`is_declared`) whenever a Cairo1 class is served from the shared, process-global `ContractClassManager`/`GlobalContractCache`, explicitly to guard against a class that was cached "from a reverted block." No equivalent verification exists for Cairo0 (V0) classes, and the `FetchCompiledClasses::is_declared` contract explicitly documents that it "always returns `false`" for Cairo0 classes, making such verification structurally impossible for this class type. [1](#0-0) [2](#0-1) 

### Finding Description
`get_compiled_from_class_manager` first checks the class in the shared `contract_class_manager` (a process-wide `GlobalContractCache`/`RawClassCache`, not part of committed chain state): [3](#0-2) 

For non-V0 (Cairo1) classes, a cache hit is followed by `self.state_reader.is_declared(class_hash)` to confirm the class is actually declared in the canonical, committed state — the code comment explicitly states this exists because "existence in the cache does not guarantee that (it might contain a declared class from a reverted block, for example)." For `RunnableCompiledClass::V0(_)`, this branch is a no-op — the cached class is trusted and returned unconditionally. [4](#0-3) 

The `is_declared` trait method is documented to be incapable of validating Cairo0 classes at all ("Cairo 0 classes always return `false`"), so this is not an incidental omission but a structural gap: [5](#0-4) 

This is confirmed by the unit test `cairo_0_cached_scenario`, which explicitly encodes that a cached Cairo0 class is returned successfully with **no** `is_declared` call and **no** `get_compiled_classes` call — i.e., the canonical state is never consulted once a V0 class is warm in the cache: [6](#0-5) 

Contrast with the Cairo1 case where a reorg causing `is_declared` to return `false` results in `UndeclaredClassHash`, i.e., the intended, safe behavior that Cairo0 lacks: [7](#0-6) 

The `ContractClassManager`/`GlobalContractCache` backing this is process-global and long-lived, explicitly shared across the batcher's block-production path and view-call path ("The class cache is shared with block production, so a view call fetches and compiles only the classes neither has seen yet"): [8](#0-7) [9](#0-8) 

**Mechanics of the divergence:** During speculative/candidate block execution (block building attempts that may later be discarded due to consensus round changes, or via `call_contract`/view-call/simulate paths sharing the same cache), a Cairo0 class declared (or referenced) by an in-progress candidate block gets pulled into the state reader and, on a cache miss, stored via `set_and_compile` into the shared `ContractClassManager` cache — independent of whether that candidate block is ever actually committed. If the candidate is discarded (never committed to storage), the on-chain state never records the class as declared, but the process-local cache retains it indefinitely (subject to LRU eviction). Any later transaction on that same node referencing the same class hash (e.g., a `deploy`/`replace_class` targeting that class, or an invoke on a contract deployed with it) will hit `get_compiled_from_class_manager`, get a V0 cache hit, skip validation entirely, and be treated as if the class were canonically declared — this diverges from every other node in the network that does not happen to have that exact stale cache entry.

### Impact Explanation
This breaks the sequencer determinism invariant: two honest nodes executing the same transaction against the same canonical state can produce different results (one treats an undeclared class as declared and executes it; another correctly rejects with `UndeclaredClassHash`), leading to different state diffs/state roots for the same block — i.e., wrong committed root/honest-node divergence, one of the explicitly in-scope impacts. In the worst case, this permits a class that was never actually declared on-chain to back a live, executable contract deployment on the affected node, an unauthorized state transition relative to the rest of the network.

### Likelihood Explanation
Reachability requires only ordinary user-submitted transactions (a Declare of a Cairo0 class plus a subsequent Deploy/Invoke/replace-class referencing that same class hash) and relies on normal sequencer behavior (speculative/candidate block building, or shared view-call caching) that discards uncommitted work while leaving the process-global class cache populated — no privileged/malicious operator, prover, or network-level behavior is required. The precise timing window (candidate block execution followed by discard, then a same-node follow-up transaction hitting the warm cache) constrains but does not eliminate practical likelihood, especially since the same cache is explicitly shared with the always-available view-call/`call_contract` path in the batcher, which is far easier for a user to trigger repeatedly than to win a race against block-building/consensus timing.

### Recommendation
Extend the `FetchCompiledClasses` trait so `is_declared` (or an equivalent canonical-state check) can validate Cairo0 classes as well, and apply the same re-verification currently done for non-V0 classes to the `RunnableCompiledClass::V0(_)` branch in `get_compiled_from_class_manager`, removing the `{}`no-op arm. Alternatively, ensure the global class cache is only populated from state reads backed by committed/canonical storage (never from in-flight/speculative candidate block state), so no class can enter the shared cache before it is durably declared.

### Proof of Concept
Not independently executable from static analysis alone; a runtime PoC would require driving the batcher through a speculative-block/candidate execution cycle that is discarded, followed by a request referencing the same Cairo0 class hash. This exact asymmetry is already codified as expected behavior in the repository's own test suite: `cairo_0_cached_scenario` versus `cached_but_verification_failed_after_reorg_scenario` in `crates/blockifier/src/state/state_reader_and_contract_manager_test.rs`, which demonstrates the reorg-safety check exists for Cairo1 but is absent for Cairo0. [10](#0-9)

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

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L66-88)
```rust
    fn get_compiled_from_class_manager(
        &self,
        class_hash: ClassHash,
    ) -> StateResult<RunnableCompiledClass> {
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
        self.increment_cache_miss_metric();
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager_test.rs (L207-240)
```rust
#[cfg(not(feature = "cairo_native"))]
fn cached_but_verification_failed_after_reorg_scenario() -> GetCompiledClassTestScenario {
    GetCompiledClassTestScenario {
        expectations: GetCompiledClassTestExpectation {
            get_compiled_classes_result: None,
            is_declared_result: Some(Ok(false)), // Verification fails after reorg.
        },
        expected_result: Err(StateError::UndeclaredClassHash(*DUMMY_CLASS_HASH)),
    }
}

#[cfg(not(feature = "cairo_native"))]
fn cairo_0_declared_scenario() -> GetCompiledClassTestScenario {
    GetCompiledClassTestScenario {
        expectations: GetCompiledClassTestExpectation {
            get_compiled_classes_result: Some(Ok(CompiledClasses::from_runnable_for_testing(
                RunnableCompiledClass::test_deprecated_casm_contract_class(),
            ))),
            is_declared_result: None,
        },
        expected_result: Ok(RunnableCompiledClass::test_deprecated_casm_contract_class()),
    }
}

#[cfg(not(feature = "cairo_native"))]
fn cairo_0_cached_scenario() -> GetCompiledClassTestScenario {
    GetCompiledClassTestScenario {
        expectations: GetCompiledClassTestExpectation {
            get_compiled_classes_result: None,
            is_declared_result: None,
        },
        expected_result: Ok(RunnableCompiledClass::test_deprecated_casm_contract_class()),
    }
}
```

**File:** crates/apollo_batcher/src/batcher.rs (L2068-2076)
```rust
        // The class cache is shared with block production, so a view call fetches and compiles only
        // the classes neither has seen yet. Its hits and misses are counted apart from block
        // production's, which view calls would otherwise dominate.
        Box::new(StateReaderAndContractManager::new_with_native_classes_whitelist(
            apollo_reader,
            self.contract_class_manager.clone(),
            native_classes_whitelist,
            Some(BATCHER_VIEW_CALL_CLASS_CACHE_METRICS),
        ))
```

**File:** crates/starknet_api/src/class_cache.rs (L10-37)
```rust
// TODO(Yoni, 1/2/2025): consider defining CachedStateReader.
/// Thread-safe LRU cache for contract classes (Sierra or compiled Casm/Native), optimized for
/// inter-language sharing when `blockifier` compiles as a shared library.
#[derive(Clone, Debug)]
pub struct GlobalContractCache<T: Clone>(pub Arc<Mutex<ContractLRUCache<T>>>);

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

    pub fn clear(&mut self) {
        self.lock().cache_clear();
    }

    pub fn new(cache_size: usize) -> Self {
        Self(Arc::new(Mutex::new(ContractLRUCache::<T>::with_size(cache_size))))
    }
```
