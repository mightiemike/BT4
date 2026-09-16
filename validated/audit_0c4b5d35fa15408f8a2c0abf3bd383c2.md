### Title
Global class cache skips declaration re-verification for Cairo0 (deprecated) classes, allowing execution of undeclared/reverted classes - ([File: crates/blockifier/src/state/state_reader_and_contract_manager.rs])

### Summary
`StateReaderAndContractManager::get_compiled_from_class_manager` re-checks `is_declared` against the current state only for Cairo1 (`RunnableCompiledClass::V0(_) => {}` skips the check, `_ => { ... is_declared ... }` runs it for everything else). The comment explains the reason for the check: a class can be cached "from a reverted block", so cache membership alone does not prove the class is declared in the *current* state. That same reasoning applies equally to Cairo0/deprecated classes, but the code explicitly exempts them from the check. [1](#0-0) 

### Finding Description
The `GlobalContractCache` (and the `ContractClassManager`/`NativeClassManager`/`TrivialClassManager` built on top of it) is a process-wide, block-agnostic LRU cache keyed only by `ClassHash`, shared across all state-reader instances and across all blocks/forks a process observes over its lifetime. [2](#0-1) 

When resolving a class for execution, `get_compiled_from_class_manager` first checks this shared cache. If present, it is returned immediately as executable — but only Cairo1 (`RunnableCompiledClass::V1`/`V1Native`) results are re-validated against `state_reader.is_declared(class_hash)`. Cairo0 (`RunnableCompiledClass::V0`) cache hits bypass this check entirely and are returned unconditionally. [3](#0-2) 

The project's own tests acknowledge the underlying hazard this check exists for — a class cached from a block that later reverts must not be treated as declared just because it's still in the cache: [4](#0-3) 

Because a long-lived sequencer/batcher process reuses one `ContractClassManager` across many blocks (including view-call paths, per the batcher's factory comments), and the cache is never cleared on a state revert (no `contract_class_manager.clear()` call was found anywhere in the batcher's revert path), any Cairo0 class hash that was ever fetched into the cache — e.g., via a declare that later gets reverted (L1/L2 reorg or reverted block), or via a speculative/aborted execution path that queried `get_compiled_classes` for a not-yet-committed class — remains servable as "declared" forever afterward, without the state actually containing that declaration. This directly mirrors the Liferay analog: a resource lookup (`search`/cache lookup) that is not properly scoped to the correct "instance" (here: the current canonical state) and thus returns/authorizes access to an object (a compiled class) that should not be visible/usable in that context.

### Impact Explanation
If a Cairo0 class becomes declared-then-reverted (or is cached via a speculative path before being committed) it can, through this cache, later be treated by the executing node as executable even though `is_declared` would return false for it in the canonical state. This can:
- Cause an account/contract to be deployed with, or a contract to be replaced to, a class hash that is not actually declared in the committed state, letting the sequencer execute code the protocol says should be `UndeclaredClassHash`.
- Cause divergence between the fresh-cache node and a node whose cache happens to hold that stale entry (unauthorized state action / wrong execution result), because whether the bug triggers depends on a node's incidental cache history rather than deterministic state — a form of honest-node divergence risk, since two honest sequencers with different cache histories could execute the same transaction differently (one succeeding via the stale cache, one correctly rejecting with `UndeclaredClassHash`), producing different state diffs/roots for the same block.

This satisfies the required impact bar (honest-node divergence / wrong committed root, unauthorized contract-class usage) rather than being a mere availability or resource issue.

### Likelihood Explanation
Reaching this requires a class hash to exist in the shared cache while not being declared in the canonical state at the time of a later lookup. This is plausible via: a declare transaction in a block that is later reverted (revert flows exist in the codebase, e.g. `apollo_reverts`, batcher revert), or via any speculative/aborted execution/view-call path that calls `get_compiled_classes`/`set_and_compile` for a class hash before it is actually committed (e.g., the view-call cache is explicitly documented as shared with block production: "The class cache is shared with block production..."). The exact conditions needed to populate the cache with an undeclared entry (which speculative paths call `set_and_compile` before commit, and whether the batcher clears the cache on revert) were not fully confirmed from the available search results — this is the main remaining uncertainty; I was not able to find a `contract_class_manager.clear()` call in `apollo_batcher/src/batcher.rs`'s revert handling within the available tool budget, which is required to state definitively whether this is unconditionally reachable in production or requires an unusual node-cache history.

### Recommendation
Apply the same `is_declared` re-verification to `RunnableCompiledClass::V0` cache hits as is already applied to Cairo1 variants in `get_compiled_from_class_manager`, i.e. remove the `V0(_) => {}` exemption so every cache hit is checked against `state_reader.is_declared(class_hash)` before being trusted as executable. Additionally, verify/ensure the global contract-class cache is invalidated (or per-entry re-validated) on state reverts and on any speculative execution path that populates it before a class's declaration is actually committed.

### Proof of Concept
Conceptual reproduction (exact reachability depends on confirming a production path that populates the cache pre-commit, noted as unresolved above):
1. Cache a Cairo0 class into the shared `ContractClassManager` for `class_hash = H` by triggering any path that calls `set_and_compile(H, CompiledClasses::V0(...))` for a not-yet-canonically-declared class (e.g. a declare in a block that is subsequently reverted, or a speculative/aborted execution referencing `H`).
2. On the same long-lived process, submit a transaction that deploys/calls a contract referencing `class_hash = H`, in a state where `state_reader.is_declared(H)` would return `false`.
3. `get_compiled_from_class_manager` hits the cache, matches `RunnableCompiledClass::V0(_) => {}`, skips the `is_declared` check, and returns the cached class as executable — successfully executing a class the current state does not consider declared, unlike the equivalent Cairo1 case which is covered by the existing `cached_but_verification_failed_after_reorg_scenario` test. [5](#0-4)

### Citations

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L65-104)
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

**File:** crates/starknet_api/src/class_cache.rs (L10-29)
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
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager_test.rs (L207-216)
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
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager_test.rs (L218-229)
```rust
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
```
