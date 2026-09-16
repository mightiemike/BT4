### Title
Stale/undeclared Cairo0 class served from process-global `ContractClassManager` cache bypasses declaration check on cache hit - (File: `crates/blockifier/src/state/state_reader_and_contract_manager.rs`)

### Summary
`StateReaderAndContractManager::get_compiled_from_class_manager` re-validates a cache hit against the current state's declaration status only for Cairo1 classes. For Cairo0 (`RunnableCompiledClass::V0`) classes, a cache hit is returned unconditionally, with no check that the class hash is actually declared in the state being executed against. Because the underlying `ContractClassManager`/`GlobalContractCache` is a process-wide, long-lived cache shared across many block-building/validation attempts (including speculative or ultimately-discarded proposals), a Cairo0 class hash that was cached during one execution context can be replayed as "declared" in a later, unrelated execution context where it was never actually declared, exactly mirroring the DNSSEC report's pattern of an insecure/no-signature branch being trusted without checking whether the underlying trust chain actually supports that trust, and a validation cache keyed too coarsely (by name/class hash alone) being reused across unrelated proof/validation scopes.

### Finding Description
`get_compiled_from_class_manager` in `crates/blockifier/src/state/state_reader_and_contract_manager.rs:66-104` implements the cache lookup: [1](#0-0) 

The `match` at lines 73-83 explicitly special-cases `RunnableCompiledClass::V0(_)` to do nothing (`{}`), while every non-V0 (Cairo1) variant is re-verified against `self.state_reader.is_declared(class_hash)` before being trusted: [2](#0-1) 

The comment on lines 76-78 documents the underlying threat model directly: cache existence does not guarantee current declaration, "it might contain a declared class from a reverted block, for example." That safeguard is applied only to Cairo1. `is_declared` itself is documented, and implemented in `apollo_state.rs`, to only ever check the Cairo1-specific class-definition table and to always return `false` for Cairo0 classes: [3](#0-2) [4](#0-3) 

Because `is_declared` cannot correctly answer the question for Cairo0, the code author chose to skip verification for V0 entirely rather than adding an equivalent Cairo0-specific declaration check — the exact analogue of Blocky's "no RRSIG ⇒ Insecure" shortcut: absence of a way to prove the positive case is treated as an implicit pass rather than triggering a proper (Cairo0-specific) proof of declaration.

The cache being bypassed is the `ContractClassManager`'s `GlobalContractCache` (`crates/blockifier/src/state/global_cache.rs`, `crates/starknet_api/src/class_cache.rs:14`), an LRU cache keyed only by `ClassHash`, with no scoping by block, proposal round, or "committed vs. speculative" state: [5](#0-4) 

This cache is populated on any miss via `set_and_compile`, regardless of whether the `StateReader` backing that read reflects a state that is ultimately committed: [6](#0-5) 

The existing unit tests explicitly encode this asymmetry as expected behavior: for the Cairo1 "cached" scenario, `is_declared` is called and can return `false` after a simulated reorg, correctly producing `UndeclaredClassHash`; for the Cairo0 "cached" scenario, no `is_declared` call is made at all and the cached class is always accepted: [7](#0-6) [8](#0-7) 

### Impact Explanation
The `ContractClassManager` is constructed once and shared across the life of the sequencer/batcher process, across proposal validation attempts and block builds, per the wiki's architecture description of `apollo_batcher`/`blockifier` interaction. Any execution path that calls `get_compiled_class` for a Cairo0 class hash populates this shared cache — including execution of a proposal that is later discarded (e.g., due to consensus round-change, failed validation, or concurrent/speculative execution abort in the blockifier's optimistic-concurrency scheduler). Once cached, that Cairo0 class hash is treated as permanently valid/declared for the process lifetime, with zero re-check against the state actually being executed. A later legitimate execution (in the canonical, committed chain) that references the same class hash (e.g. via `deploy`/`replace_class` syscalls, or an Invoke that indirectly triggers a `get_compiled_class` for that class hash) will silently succeed in running attacker-controlled Cairo0 bytecode that was never declared in the committed state's declared-classes set.

This directly threatens:
- **Wrong committed state root**: the sequencer executes code for a class hash absent from the canonical declared-classes commitment, producing a state/computation result inconsistent with what a state built strictly from committed data would produce.
- **Honest-node divergence**: only nodes whose process-local cache happened to be polluted by the discarded/speculative execution will exhibit this behavior; other honest validators that never executed that discarded proposal will correctly reject the same transaction with `UndeclaredClassHash`, causing block-hash/consensus divergence between honest sequencers — the network becomes unable to reach agreement on the resulting block.

This satisfies the required "wrong committed root or block hash, honest-node divergence" impact criteria, and is reachable purely from a submitted Declare transaction (in a proposal that ends up discarded) followed by an ordinary Invoke/Deploy from any unprivileged sender referencing that class hash — no special operator/proposer/peer privilege required.

### Likelihood Explanation
The precondition (a Cairo0 class becoming cached via an execution attempt that does not correspond to the final committed state) is plausible in normal validator operation: batchers/validators execute proposals speculatively before consensus finalizes them, and round-changes/validation failures routinely discard proposals after they have been executed once (the comment in the source code itself acknowledges "a declared class from a reverted block" as a known scenario). The bug requires no upstream tampering or malicious infrastructure — it is a straightforward gap in the existing, otherwise-correct Cairo1 safeguard, deliberately left unaddressed for Cairo0. Exploitation only requires crafting an ordinary Declare (Cairo0) transaction and getting it executed once by a target sequencer under conditions that don't finalize it, then following up with a transaction referencing the same class hash.

### Recommendation
Add a Cairo0-equivalent declaration re-check on every cache hit, mirroring the Cairo1 path in `get_compiled_from_class_manager`: extend `FetchCompiledClasses::is_declared` (or add a new method) to also authoritatively answer "is this specific class hash declared as Cairo0 in the current state" (e.g., using the deprecated-class-definition block-number lookup analogous to `get_class_definition_block_number`, but for the deprecated/Cairo0 table), and call it unconditionally for `RunnableCompiledClass::V0` cache hits just as is done for non-V0 variants. Alternatively, scope/invalidate the `GlobalContractCache` per accepted block commit rather than keeping it valid indefinitely across discarded speculative executions.

### Proof of Concept
1. Attacker submits Declare (Cairo0) transaction `D` with class `C` (class hash `H`) to a target sequencer as part of a proposal `P1` in round `R`.
2. The sequencer's `apollo_batcher`/`blockifier` executes `P1` for validation, calling `get_compiled_class(H)` during transaction execution; `get_compiled_from_class_manager` misses, fetches, and calls `set_and_compile(H, ...)`, inserting `C` into the shared, process-global `ContractClassManager` cache (`crates/blockifier/src/state/state_reader_and_contract_manager.rs:90-91`).
3. Proposal `P1` is discarded (e.g., round-change, competing proposal wins, or validation abort) — `H` is never actually committed to persistent state, so `is_declared`-equivalent-for-Cairo0 state would say "not declared."
4. Attacker submits a follow-up transaction (Invoke/Deploy/ReplaceClass) referencing class hash `H` in a later, actually-committed block.
5. `get_compiled_class(H)` is called again; `get_compiled_from_class_manager` hits the cache, sees `RunnableCompiledClass::V0(_)`, and — per lines 73-74 of `state_reader_and_contract_manager.rs` — returns the cached class with **no** declaration check, executing `C`'s bytecode as though it were legitimately declared, even though the committed state never recorded `H` as declared.
6. Any honest node that did not happen to execute `P1` (and thus never cached `H`) will instead correctly return `StateError::UndeclaredClassHash(H)` for the same transaction, causing divergent execution results/state roots between honest sequencers.

### Citations

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L19-22)
```rust
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

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L88-96)
```rust
        self.increment_cache_miss_metric();

        let compiled_class = self.state_reader.get_compiled_classes(class_hash)?;
        self.contract_class_manager.set_and_compile(class_hash, compiled_class.clone());
        // Access the cache again in case the class was compiled.
        let runnable_class = self
            .contract_class_manager
            .get_runnable(&class_hash, &self.native_classes_whitelist)
            .unwrap_or_else(|| {
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

**File:** crates/starknet_api/src/class_cache.rs (L12-22)
```rust
/// inter-language sharing when `blockifier` compiles as a shared library.
#[derive(Clone, Debug)]
pub struct GlobalContractCache<T: Clone>(pub Arc<Mutex<ContractLRUCache<T>>>);

impl<T: Clone> GlobalContractCache<T> {
    /// Locks the cache for atomic access. Although conceptually shared, writing to this cache is
    /// only possible for one writer at a time.
    pub fn lock(&self) -> LockedClassCache<'_, T> {
        self.0.lock().expect("Global contract cache is poisoned.")
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

**File:** crates/blockifier/src/state/state_reader_and_contract_manager_test.rs (L231-240)
```rust
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
