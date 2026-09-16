### Title
Stale global compiled-class cache entries are trusted without re-validation for Cairo0 (V0) classes, unlike Cairo1/Native classes - (File: crates/blockifier/src/state/state_reader_and_contract_manager.rs)

### Summary
`StateReaderAndContractManager::get_compiled_from_class_manager` consults a long-lived, process-global compiled-class cache (`ContractClassManager` / `GlobalContractCache`, an `Arc<Mutex<..>>` that is not scoped to a single block or execution attempt) before querying the authoritative state reader. The code explicitly acknowledges that a cache hit does not prove the class is currently declared — "it might contain a declared class from a reverted block" — and re-validates via `is_declared()` for Cairo1/Native classes. That re-validation branch is empty for Cairo0 (`V0`) classes, so a stale cache entry for a V0 class is trusted unconditionally.

### Finding Description
`get_compiled_from_class_manager` [1](#0-0)  looks up `class_hash` in `self.contract_class_manager` (a process-global cache backed by `GlobalContractCache`/`NativeClassManager`, an `Arc<Mutex<..>>` shared across every block, proposal, and validation call in the process) [2](#0-1) [3](#0-2) .

On a cache hit, the code branches on the runnable class variant:
```
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
``` [4](#0-3) 

This comment is a direct admission that the cache is an object whose lifetime/validity is decoupled from the state/"namespace" (block, proposal, or validation attempt) that populated it — the exact bug class in the reference CVE, where a reference-counted kernel object (`ucounts`) can outlive the namespace that created it and get reused in a stale/invalid context. Here, for the Cairo1/Native path this hazard is explicitly compensated for by re-checking `is_declared` against the current state before trusting the cache. For the `V0` branch, no equivalent check exists, so a cached Cairo0 compiled class is served as-is regardless of whether the current (canonical) state actually has it declared.

The cache is populated whenever `self.state_reader.get_compiled_classes(class_hash)` succeeds [5](#0-4) , which can happen during any execution/validation attempt against a not-yet-committed or later-discarded state (e.g., a block proposal that is ultimately rejected/reverted, or a validate-only/simulation execution) — the very scenario the surrounding comment calls out. Because the cache is process-global and not cleared per block/attempt, such a Cairo0 entry persists and is later returned for any transaction referencing that `class_hash`, even in future blocks built on a completely different canonical state.

### Impact Explanation
If a Cairo0 class is compiled and cached during a proposal or validation attempt that is ultimately not the one that gets committed (reverted block / discarded proposal / simulate-only execution), the compiled class remains in the shared global cache. A later transaction that references the same `class_hash` (e.g., via `library_call`, or a deployed contract using that class hash) can be executed using this stale, unverified compiled class without the canonical state confirming the class is declared. This:
- Breaks the "declare-before-use" invariant specifically for Cairo0 classes, allowing code to run at a class hash that is not actually declared in the committed chain state.
- Causes honest-node divergence: sequencer nodes with a warm/stale cache entry will execute the class successfully, while nodes without that cache entry will (correctly) reject it as `UndeclaredClassHash`, leading to different execution results / state roots for the same transaction across the network.
- Can result in unauthorized contract logic execution and, depending on what that logic does, unauthorized account actions or asset movement premised on code that was never legitimately declared.

### Likelihood Explanation
Root cause and the asymmetry between the V0 and V1/Native paths are clearly present in code, and the surrounding comment explicitly documents the exact "stale cache outlives its originating context" hazard for the general case. However, I could not fully trace, within the available search budget, every caller path that populates this specific global cache (e.g., whether gateway/mempool validate-only or simulation calls definitely route through this exact `ContractClassManager` instance, versus a separate per-request cache) to conclusively prove an unprivileged attacker can single-handedly force a discarded/reverted proposal to populate this cache with a Cairo0 class of their choosing. This should be verified against the full call graph of `set_and_compile`/`ContractClassManager` across `apollo_gateway`, `apollo_batcher`, and `native_blockifier` before treating this as fully proven end-to-end; the missing V0 revalidation itself, however, is a clear and precisely located gap.

### Recommendation
Apply the same `is_declared`-style re-validation against the authoritative state reader for `RunnableCompiledClass::V0` cache hits as is already done for the `_` (Cairo1/Native) branch in `get_compiled_from_class_manager`, or otherwise scope/invalidate the global contract-class cache to the committed state it was derived from so cached entries cannot outlive the block/proposal context that produced them.

### Proof of Concept
Conceptual reproduction (not fully verified against every caller in the codebase within this investigation):
1. Trigger compilation/caching of a Cairo0 class `X` via an execution/validation path that reads `get_compiled_classes(X)` successfully but whose resulting block/proposal/validation is subsequently discarded or reverted (as explicitly anticipated by the comment at [6](#0-5) ), so that `X` is never actually declared in the canonical committed state.
2. Submit a subsequent transaction on the canonical (undeclared-for-`X`) state that invokes class `X` (e.g., via `library_call` or a contract address mapped to class hash `X`).
3. `get_compiled_class` hits the process-global cache, matches `RunnableCompiledClass::V0`, and returns `Ok(runnable_class)` without checking `is_declared` against the real state [7](#0-6) , executing code that should have been rejected with `StateError::UndeclaredClassHash`.

### Citations

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L65-87)
```rust
impl<S: FetchCompiledClasses> StateReaderAndContractManager<S> {
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
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L88-91)
```rust
        self.increment_cache_miss_metric();

        let compiled_class = self.state_reader.get_compiled_classes(class_hash)?;
        self.contract_class_manager.set_and_compile(class_hash, compiled_class.clone());
```

**File:** crates/starknet_api/src/class_cache.rs (L13-37)
```rust
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

**File:** crates/blockifier/src/state/native_class_manager.rs (L48-61)
```rust
/// Manages the global cache of contract classes and handles sierra-to-native compilation requests.
#[derive(Clone)]
pub struct NativeClassManager {
    cairo_native_run_config: CairoNativeRunConfig,
    /// The global cache of raw contract classes.
    class_cache: RawClassCache,
    /// The global cache of compiled class hashes v2.
    compiled_class_hash_v2_cache: GlobalContractCache<CompiledClassHash>,
    /// The sending half of the compilation request channel. Set to `None` if native compilation is
    /// disabled.
    sender: Option<SyncSender<CompilationRequest>>,
    /// The sierra-to-native compiler.
    compiler: Option<Arc<SierraToNativeCompiler>>,
}
```
