## Analysis

The CVE describes a bug class where a **cached/renewed credential bypasses a revocation check** — the system continues trusting something it should have invalidated. Searching the sequencer for an analogous pattern where a cached, previously-valid artifact bypasses re-validation against current committed state led to `crates/blockifier/src/state/state_reader_and_contract_manager.rs`.

### Title
Stale Cairo0 (V0) compiled-class cache entries bypass declaration re-verification, enabling honest-node state divergence — (File: `crates/blockifier/src/state/state_reader_and_contract_manager.rs`)

### Summary
`StateReaderAndContractManager::get_compiled_from_class_manager` re-verifies that a cached **Cairo1** class is still actually declared in the current committed state before trusting the cache hit, guarding against exactly the scenario where a class was cached from a block/proposal that never got committed (e.g., reverted). This guard is explicitly skipped for **Cairo0 (V0)** classes, leaving no revocation/staleness check at all for that class type.

### Finding Description
In `get_compiled_from_class_manager`: [1](#0-0) 

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
}
```

The comment on the same file's `FetchCompiledClasses::is_declared` explains why V0 is excluded — the method is Cairo1-specific and "Cairo 0 classes always return `false`": [2](#0-1) 

This means Cairo0 classes have **no equivalent staleness check at all**. The `ContractClassManager`/global class cache (`RawClassCache` / `GlobalContractCache`) is a process-global, long-lived cache shared across transaction validation, speculative block execution, and re-execution attempts: [3](#0-2) 

If a Cairo0 `Declare` transaction is compiled and cached via `set_and_compile` during a speculative/attempted block execution or gateway validation, but that particular execution attempt is never actually committed to state (e.g., a proposed block is rejected by consensus, or the declaring transaction is otherwise reverted/replaced before commit), the compiled class entry for that `class_hash` remains resident in the cache. A subsequent transaction referencing that class hash (e.g., `deploy`, `library_call`, or `replace_class`) will hit the cache and, because the V0 branch performs no `is_declared` re-check, execution proceeds as if the class were legitimately declared — even though the canonical committed state has no such declaration.

The test suite explicitly documents the fix for the Cairo1 analog of this exact scenario, confirming the bug class is understood but only patched for one class type: [4](#0-3) 

### Impact Explanation
A node whose cache still holds the stale Cairo0 class will successfully execute code paths (deploy/library_call/replace_class) that reference an undeclared class hash, while a node without that stale cache entry (e.g., one that never attempted the reverted proposal, or one with a smaller/evicted cache) will reject the same transaction as referencing an undeclared class. This produces **honest-node execution divergence**: different nodes compute different state transitions (or different rejection outcomes) for the identical transaction, which can lead to divergent state roots/block hashes and a chain unable to reach consensus on the resulting block — a valid Medium/High-impact analog per the rules (concrete state-root/consensus divergence), directly mirroring the CVE's "should-have-been-invalidated-but-wasn't" bug class.

### Likelihood Explanation
Reaching this requires only ordinary, unprivileged actions: submitting a Cairo0 `Declare` transaction whose execution/validation gets cached but is not committed (a routine occurrence in speculative block-building/proposal-rejection flows or transaction re-validation across mempool/gateway retries), followed by a normal transaction referencing that same, now-uncommitted class hash. No special privileges, node compromise, or network-level attack is needed — only crafted transaction sequencing from a standard sender.

### Recommendation
Apply the same declaration re-verification currently done for Cairo1 classes to Cairo0 classes as well, using a Cairo0-capable equivalent of `is_declared` (e.g., checking `get_class_hash_at`/deployed-class or a dedicated "declared" flag in the state reader rather than the Cairo1-only `is_declared`), so that cache hits for V0 classes are also validated against the current committed state before being trusted.

### Proof of Concept
1. Submit a Cairo0 `Declare` transaction (`class_hash = X`) that gets included in a speculative execution/proposal (compiling and caching `X` into the process-global `ContractClassManager` via `set_and_compile`), but ensure this proposal/execution is ultimately not committed (e.g., proposal rejected, block re-proposed without it, or tx dropped due to a race/rewind in the mempool as seen in `crates/apollo_mempool/src/mempool.rs`'s `commit_block`/rewind logic).
2. On the same node process (cache still warm), submit a follow-up transaction that references `class_hash = X` via `deploy`, `library_call`, or `replace_class`.
3. Observe that `get_compiled_from_class_manager` returns the cached V0 `RunnableCompiledClass` without calling `is_declared`, so execution proceeds successfully.
4. On a peer node that never cached `X` (or evicted it), the same transaction is rejected with `StateError::UndeclaredClassHash`, demonstrating divergent execution outcomes between honest nodes for an identical transaction.

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

**File:** crates/blockifier/src/state/contract_class_manager.rs (L41-55)
```rust
        pub fn get_runnable(
            &self,
            class_hash: &ClassHash,
            _native_classes_whitelist: &NativeClassesWhitelist,
        ) -> Option<RunnableCompiledClass> {
            Some(self.class_cache.get(class_hash)?.to_runnable())
        }

        pub fn set_and_compile(&self, class_hash: ClassHash, compiled_class: CompiledClasses) {
            self.class_cache.set(class_hash, compiled_class);
        }

        pub fn clear(&mut self) {
            self.class_cache.clear();
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
