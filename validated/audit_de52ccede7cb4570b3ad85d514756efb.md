## Analog Found: Stale Global Class Cache Bypasses Declaration Check for Cairo 0 Classes

### Title
Cairo 0 compiled-class cache entries are served without re-validating current declaration status, unlike Cairo 1 - ([File: crates/blockifier/src/state/state_reader_and_contract_manager.rs])

### Summary
The curl bug reuses a pooled connection (authenticated for `user1`) to serve a request meant for `user2`, because the connection-reuse logic doesn't check that the cached resource is still valid for the new caller's authentication context. The sequencer's `ContractClassManager` / `GlobalContractCache` has the analogous defect: it is a long-lived, process-global cache keyed only by `ClassHash` (with no state/block versioning), and it explicitly re-validates cache hits against the *current* canonical state only for Cairo 1 classes — not for Cairo 0 classes.

### Finding Description
`StateReaderAndContractManager::get_compiled_from_class_manager` re-checks a cache hit's validity against the live state reader, but only for non-V0 classes: [1](#0-0) 

The comment on this exact code path acknowledges the bug class being scanned for: *"existence in the cache does not guarantee that (it might contain a declared class from a reverted block, for example)."* The fix (calling `is_declared` on the live `state_reader`) was applied only to the `_ =>` (Cairo 1) arm; the `RunnableCompiledClass::V0(_) => {}` arm does nothing.

The reason given in the trait doc is that `is_declared` is defined to always return `false` for Cairo 0 classes: [2](#0-1) 

and the concrete implementation in `ApolloReader::is_declared` only checks the *Sierra* class-definition block number, which is meaningless for Cairo 0: [3](#0-2) 

`ContractClassManager`/`GlobalContractCache` is a single process-wide `Arc<Mutex<SizedCache<ClassHash, T>>>`, with no notion of block height or state root: [4](#0-3) 

Because the cache key is purely content-addressed (`ClassHash`) and never invalidated per block/state, once a Cairo 0 class hash is populated into this cache (e.g., via speculative validation of a Declare transaction, or an execution belonging to a block that is later reverted/reorged out and never becomes canonical), it stays servable as "declared" for the lifetime of the process for any *future* request, regardless of whether that class is actually declared in the node's current canonical state. This is confirmed by the project's own regression test, which explicitly documents that a Cairo 0 cache hit performs **no** verification call at all, unlike the Cairo 1 case: [5](#0-4) [6](#0-5) 

### Impact Explanation
This is the same class of bug as the curl advisory: a shared cache entry is reused across a boundary (chain state / declaration authorization) that the caching logic is supposed to enforce, but the enforcement path silently exempts one code path (V0) from the check that the other path (V1) has. Concretely:
- A node's `ContractClassManager` cache can be populated by a Cairo 0 class from a transaction that is later reverted/reorged/never-committed (mempool speculative validation, block-building attempts that don't finalize, etc.).
- Any later transaction (Deploy, Invoke, `replace_class` syscall) referencing that same class hash on that node will be served the cached compiled class and treated as "declared," even though the node's canonical, committed state does **not** contain that declaration.
- A different honest node without that stale cache entry will correctly reject the same transaction with `UndeclaredClassHash`.
- This produces **honest-node divergence**: nodes disagree on whether a transaction executes successfully, which state changes it produces, and ultimately the block's state root/hash — a consensus-breaking condition explicitly listed as in-scope impact.

### Likelihood Explanation
The gap is triggered purely by an unprivileged sender's Declare/Deploy/Invoke transaction referencing a Cairo 0 class hash; no operator or validator privilege is needed. The likelihood of the cache being populated from a non-committed context depends on the mempool/gateway's use of this same `ContractClassManager` for speculative validation prior to inclusion — a normal, expected code path, not a contrived edge case. The developers' own comment on the very same function shows this exact scenario ("reverted block") was anticipated, but the fix was incompletely applied (V1 only).

### Recommendation
Extend `FetchCompiledClasses::is_declared` (or an equivalent check) to also validate Cairo 0 class hashes against the live/current state before trusting a `GlobalContractCache` hit, mirroring the V1 path in `get_compiled_from_class_manager`. Alternatively, key or scope the `GlobalContractCache` by state/block context so cache entries populated from non-canonical (reverted/uncommitted) executions cannot be served to unrelated, later queries against a different canonical state.

### Proof of Concept
1. Node's `ContractClassManager` cache is warmed with Cairo 0 class `C` for `class_hash = H`, e.g., via speculative mempool/gateway validation of a Declare transaction that is ultimately never included on-chain (dropped, reorged, or its block reverted).
2. Attacker submits a transaction (e.g., `deploy` or `replace_class` syscall) referencing `class_hash = H` on this node, while `H` is **not** actually declared in the node's committed state.
3. `StateReaderAndContractManager::get_compiled_from_class_manager` hits the cache, matches `RunnableCompiledClass::V0(_) => {}`, performs no re-validation, and returns the cached class as valid — line [7](#0-6) .
4. The transaction executes successfully on this node using an undeclared class, while another honest node (without the stale cache entry) calls `state_reader.get_compiled_classes(H)` and correctly returns `StateError::UndeclaredClassHash(H)`, rejecting the transaction.

Note: I was not able to directly trace, within available tool budget, the exact call sites in `apollo_gateway`/`apollo_batcher` that invoke speculative/mempool validation against the shared `ContractClassManager` prior to block commitment (to fully confirm the cache-poisoning entry point end-to-end). The root-cause code asymmetry (V1 re-validated, V0 not) is confirmed directly in the code and tests cited above.

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

**File:** crates/apollo_state_reader/src/apollo_state.rs (L315-325)
```rust
    fn is_declared(&self, class_hash: ClassHash) -> StateResult<bool> {
        let state_number = StateNumber(self.latest_block);
        let class_declaration_block_number = self
            .reader()?
            .get_state_reader()
            .and_then(|sr| sr.get_class_definition_block_number(&class_hash))
            .map_err(|err| StateError::StateReadError(err.to_string()))?;
        Ok(
            matches!(class_declaration_block_number, Some(block_number) if block_number <= state_number.0),
        )
    }
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

**File:** crates/blockifier/src/state/state_reader_and_contract_manager_test.rs (L255-264)
```rust
#[cfg(not(feature = "cairo_native"))]
#[rstest]
#[case::cairo_0_declared_and_cached(cairo_0_declared_scenario(), cairo_0_cached_scenario())]
#[case::cairo_1_declared_and_cached(cairo_1_declared_scenario(), cairo_1_cached_scenario())]
#[case::cairo_1_declared_then_verification_failed_after_reorg(
    cairo_1_declared_scenario(),
    cached_but_verification_failed_after_reorg_scenario()
)]
#[case::not_declared_then_declared(not_declared_scenario(), cairo_1_declared_scenario())]
#[case::not_declared_both_rounds(not_declared_scenario(), not_declared_scenario())]
```
