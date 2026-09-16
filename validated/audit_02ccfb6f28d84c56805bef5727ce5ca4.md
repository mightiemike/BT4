### Title
Stale global class cache skips `is_declared` re-verification for Cairo0 classes, allowing use of undeclared/reverted classes and honest-node divergence - (File: crates/blockifier/src/state/state_reader_and_contract_manager.rs)

### Summary
`StateReaderAndContractManager::get_compiled_from_class_manager` re-validates a cache hit against current committed state via `is_declared()` only for Cairo1 (`RunnableCompiledClass::V1`/`V1Native`) classes. For Cairo0 (`RunnableCompiledClass::V0`) classes, a cache hit is trusted unconditionally, with no re-check against the canonical state. This is structurally identical to the reported `canOffboard[term]` bug: a "sticky" boolean/derived state (here: "this class hash is compiled and usable") that is set once and is never invalidated/reset when the underlying authoritative state changes (a block/class declaration being reverted), letting a stale grant persist and later be exploited without re-passing the intended gate (declaration).

### Finding Description
`ContractClassManager` (`GlobalContractCache` / `RawClassCache`) is a **process-lifetime** cache shared across all blocks executed by a node — it is not tied to a single block's `CachedState` and is not cleared on block revert. See `crates/blockifier/src/state/native_class_manager.rs:63-153` and `crates/blockifier/src/state/contract_class_manager.rs:1-77`.

When resolving a compiled class, `get_compiled_from_class_manager` does: [1](#0-0) 

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
    ...
    return Ok(runnable_class);
}
```

The comment explicitly documents the threat model this code defends against for Cairo1: a class hash can end up in the long-lived cache because it was declared in a block that was later reverted, so on a *subsequent, unrelated* transaction the cache must be re-validated against the canonical state before being trusted. The mitigation exists **only** for the Cairo1 branch. The `FetchCompiledClasses::is_declared` trait method itself documents the gap: [2](#0-1) 

```rust
pub trait FetchCompiledClasses: StateReader {
    fn get_compiled_classes(&self, class_hash: ClassHash) -> StateResult<CompiledClasses>;

    /// Returns whether the given class hash corresponds to a declared Cairo 1 class.
    /// Cairo 0 classes always return `false`.
    fn is_declared(&self, class_hash: ClassHash) -> StateResult<bool>;
}
```

This means: for a Cairo0 (deprecated) class hash, once it enters the shared process cache (`set_and_compile`, called on the first `get_compiled_classes` miss), it is served from cache **forever**, regardless of whether the block that declared it is later reverted (reorg, invalid-block rollback, aborted proposal re-execution, etc.) and the class is no longer present in `deprecated_declared_classes` in the canonical committed state. The test suite explicitly enumerates and asserts this exact asymmetry: `cairo_1_declared_then_verification_failed_after_reorg` fails as expected, but there is no `cairo_0_...after_reorg` counterpart, and `cairo_0_declared_scenario`/`cairo_0_cached_scenario` never invoke `is_declared`: [3](#0-2) [4](#0-3) 

This is precisely the offboarding-report's bug class: a privileged/derived flag ("this class may be used without a fresh declare") is granted once and never reset when the authoritative source of truth (the committed state diff / declared-classes table) reverts that grant, and the missing re-validation is applied inconsistently (present for one code path, absent for the sibling path) — exactly like `canOffboard` being reset by `cleanup()` for the offboarding flow but not being re-checked against the freshly re-onboarded gauge state.

### Impact Explanation
Because `ContractClassManager` is a per-node, in-process, long-lived cache (not part of the versioned/committed `CachedState`), its content can diverge between honest nodes depending on their execution/revert history (e.g., one node executed and then reverted a block containing a Cairo0 declare of class `C`, another node never saw it, a third node saw and kept it committed). After the divergence point:
- A node with a stale positive Cairo0 cache entry for `C` will accept and successfully execute any subsequent transaction referencing `C` (e.g. `DeployAccount`/`deploy_syscall`/`replace_class` for Cairo0 targets, or a constructor invocation) treating the class as declared and skipping `state.get_compiled_class` failure, even though `C` is not part of the canonical, currently-committed state.
- A node without that stale cache entry will correctly return `StateError::UndeclaredClassHash` and reject/re-execute the transaction differently.

This produces **honest-node divergence**: two conforming nodes reach different execution results (success vs. `UndeclaredClassHash` revert) for the identical block/transaction, which can lead to disagreement on the resulting state diff, the committed state root, and consequently block hash/consensus agreement for that block. It can also let a contract be deployed/operate using a class that is not actually recorded as declared in the canonical state (an unauthorized action bypassing the declare-then-use invariant), and the resulting state changes attributed to a "phantom" undeclared class are not properly reflected on-chain, risking incorrect state commitment.

### Likelihood Explanation
Block re-execution/reverts are a normal, non-malicious occurrence in a sequencer (aborted proposals, failed candidate blocks, batcher re-execution/retry flows, and reorg handling) — this is not a "malicious operator/proposer" precondition; the process-wide class cache is explicitly designed to survive across such block boundaries (that is the entire reason the Cairo1 mitigation and its regression test, `cached_but_verification_failed_after_reorg_scenario`, exist). The trigger requires only: (1) a Cairo0 declare transaction is executed and cached in a block/proposal that is later reverted, and (2) a later transaction referencing the same Cairo0 class hash is submitted to the same node. Both are reachable purely from submitted transactions with no special privileges, matching the required "reachable from a single submitted transaction" scope. Likelihood is somewhat tempered by the requirement of a preceding revert event and by the declining prevalence of Cairo0 declares, but the underlying mechanism is deterministic and directly exercised by existing negative tests for the Cairo1 sibling path, confirming the team is aware of and defends against this exact class of issue — just not symmetrically.

### Recommendation
Extend the reorg-safety check performed for Cairo1 classes to Cairo0 classes as well: either (a) make `is_declared` cover deprecated/Cairo0 classes too (drop the "Cairo0 always returns false" carve-out) and always re-validate cache hits against the canonical state regardless of class version, or (b) invalidate/clear the relevant `GlobalContractCache` entries whenever the corresponding block is reverted, so that a cache hit can never outlive the state that produced it. Add a regression test mirroring `cairo_1_declared_then_verification_failed_after_reorg` for the Cairo0 path (a `cairo_0_declared_then_verification_failed_after_reorg_scenario`) to lock in the fix.

### Proof of Concept
Conceptual reproduction, mirroring the existing test harness in `crates/blockifier/src/state/state_reader_and_contract_manager_test.rs`:
1. First round: `state_reader.get_compiled_classes(class_hash)` returns `Ok(CompiledClasses::V0(...))` (a Cairo0 declare was executed); `get_compiled_from_class_manager` caches it via `set_and_compile` and returns the runnable class. This populates the shared, block-independent `ContractClassManager` cache.
2. Simulate a revert: the block/state diff that declared `class_hash` is dropped from the canonical committed state (e.g., `deprecated_declared_classes_table` no longer contains it, as exercised by `crates/apollo_storage/src/state/state_test.rs::declare_revert_declare_scenario`), so a fresh state reader for `class_hash` would report it as undeclared.
3. Second round, same process/cache: call `get_compiled_from_class_manager(class_hash)` again with a state_reader mock that would return `UndeclaredClassHash` if queried directly. Because the cached value is `RunnableCompiledClass::V0`, the `match` arm at lines 74 (`RunnableCompiledClass::V0(_) => {}`) skips calling `is_declared`/re-validation entirely, and the function returns `Ok(runnable_class)` — the stale, no-longer-declared Cairo0 class is served as valid, whereas the equivalent Cairo1 scenario (`cairo_1_declared_then_verification_failed_after_reorg`, `state_reader_and_contract_manager_test.rs:259-262`) correctly returns `Err(StateError::UndeclaredClassHash(...))`.

This is the exact scenario the code comment at `state_reader_and_contract_manager.rs:76-78` was written to prevent — but the prevention is missing for the `V0` branch, which the accompanying trait doc (`is_declared`, lines 19-21) confirms is out of scope by design for Cairo0.

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

**File:** crates/blockifier/src/state/state_reader_and_contract_manager_test.rs (L207-229)
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
