## Title
Cairo 0 (deprecated) contract classes bypass the post-cache-hit declaration check, letting the global class cache serve undeclared classes as valid - ([File: crates/blockifier/src/state/state_reader_and_contract_manager.rs])

### Summary
`StateReaderAndContractManager::get_compiled_from_class_manager` re-verifies, on every cache hit, that a Cairo 1 class returned from the process-wide `ContractClassManager` cache is actually declared in the *current* committed state — explicitly because the cache "might contain a declared class from a reverted block" [1](#0-0) . For Cairo 0 (`RunnableCompiledClass::V0`) classes this re-verification is explicitly skipped (`RunnableCompiledClass::V0(_) => {}`) [2](#0-1) . Because the underlying `ContractClassManager`/`GlobalContractCache` is a long-lived, process-wide LRU cache shared across many independent state readers and block/validation contexts [3](#0-2) [4](#0-3) , a Cairo 0 class that ever got cached (e.g. via a speculative/aborted validation or an uncommitted block-building attempt) will be served indefinitely as "declared" to every future, unrelated validation context, without ever re-checking the authoritative persisted state. This is directly analogous to the reported CVE: a trust decision established in one context (one "virtual host"/one candidate block) is silently reused in a different context (another virtual host / a different, later, unrelated state) without the per-context check that is supposed to gate access.

### Finding Description
`FetchCompiledClasses::is_declared` is meant to be the authoritative, per-state-context check of whether a class hash is actually declared as of the current block [5](#0-4) . Its real implementation, `ApolloReader::is_declared`, compares the class's declaration block number against the *reader's own* `latest_block`/state number [6](#0-5)  — i.e. it is state-context specific, exactly like `SSLStrictSNIVHostCheck`/per-vhost trust in the CVE. Its doc comment even claims "Cairo 0 classes always return `false`" (i.e., the check does not distinguish), yet the actual DB-backed implementation makes no such special-case for Cairo 0 — it only checks the declaration block number, contradicting the documented contract.

`get_compiled_from_class_manager` relies on `is_declared` to invalidate stale global-cache entries, but only for non-V0 classes:

```
match &runnable_class {
    RunnableCompiledClass::V0(_) => {}
    _ => {
        if !self.state_reader.is_declared(class_hash)? {
            return Err(StateError::UndeclaredClassHash(class_hash));
        }
    }
}
``` [7](#0-6) 

The comment on this exact block acknowledges the general hazard ("it might contain a declared class from a reverted block, for example") but the mitigation is applied inconsistently — only to Cairo 1. This asymmetry is confirmed by the project's own unit test matrix: the Cairo 1 "cached" scenario expects a call to `is_declared` and can fail with `UndeclaredClassHash` after a simulated reorg, while the Cairo 0 "cached" scenario expects **no** verification call at all and always succeeds [8](#0-7) .

`ContractClassManager` (the "class cache") is shared across the node's entire lifetime — used identically by the gateway's stateful validator, the batcher's block-building, and later re-validation, all of which construct new `StateReaderAndContractManager`/`CachedState` instances backed by the *same* long-lived cache [9](#0-8) . Any code path that ever calls `get_compiled_classes`/`set_and_compile` for a Cairo 0 class hash (e.g. a speculative gateway/mempool validation of a declare transaction, or a batcher block-building attempt that is later discarded/not committed) permanently seeds the cache with that class, entirely independent of whether the corresponding declare transaction is ever actually included/committed in the canonical chain.

### Impact Explanation
Once a Cairo 0 class hash is cached this way, any later, unrelated transaction on this node (invoke, deploy_account, or a contract call that triggers `library_call`/`replace_class` for that class hash) will have `get_compiled_class` return the cached CASM as if the class were properly declared, without ever consulting the authoritative persisted declaration state for that hash — bypassing:
- the declare-transaction fee/DA cost that should be paid to make a class usable,
- the gateway's declare-permission gating (`check_declare_permissions`) that only runs on the `Declare` transaction path,
- and the actual on-chain requirement that a class be declared before use.

Because this cache is process-local and not derived from committed state, an honest node that never happened to run/cache that speculative validation (or one whose cache entry was evicted) will correctly reject the same transaction with `UndeclaredClassHash`. This produces divergent transaction acceptance/execution results between otherwise-honest nodes for the exact same input transaction — a form of honest-node divergence that can lead to disagreement on the resulting state root/block hash when such transactions are included in a block and re-executed by other sequencers or by the Starknet OS.

### Likelihood Explanation
This is reachable purely by an ordinary, unprivileged sender: submit (or arrange for the gateway/batcher to speculatively process) a Cairo 0 declare transaction that ends up not being committed (e.g., rejected later in the pipeline, replaced, or used only in an aborted candidate block), then, separately, deploy or interact with a contract that references that same Cairo 0 class hash. No malicious operator, proposer, or network-level behavior is required — it stems purely from normal declare/invoke transaction processing and the node's own internal (non-committing) validation/block-building attempts.

### Recommendation
Remove the special-case exemption for `RunnableCompiledClass::V0` in `get_compiled_from_class_manager` and apply the same `is_declared` (or equivalent per-state-context) re-verification on every cache hit, regardless of Cairo version. Additionally, fix `FetchCompiledClasses::is_declared`'s implementation/documentation mismatch so it is guaranteed to correctly and uniformly validate declaration status for both Cairo 0 and Cairo 1 classes against the current state.

### Proof of Concept
1. A Cairo 0 class `C` with hash `H` is submitted for declaration but, for any reason, the declare transaction is speculatively validated/compiled by the gateway or batcher (calling into `StateReaderAndContractManager::get_compiled_from_class_manager`, which calls `set_and_compile` on the shared `ContractClassManager`) but never actually gets committed to state (e.g., it's dropped from the mempool, outcompeted by fee, or included only in a discarded/aborted block-building attempt) [10](#0-9) .
2. `H` is now permanently present in the process-wide `ContractClassManager` cache [11](#0-10) , even though `is_declared(H)` (checked against the authoritative persisted state) would return `false`.
3. Any subsequent, unrelated transaction that triggers `get_compiled_class(H)` (e.g. deploying a contract with class hash `H`, or an existing contract calling `library_call`/`replace_class` with `H`) hits the cache and, because `H` is `RunnableCompiledClass::V0`, skips the `is_declared` check entirely and succeeds [12](#0-11) .
4. A different honest node that never cached `H` (or whose cache entry expired) calls `get_compiled_classes`/`is_declared` directly against persisted state and correctly returns `UndeclaredClassHash`, rejecting the same transaction [13](#0-12) .

Note: I was not able to fully trace every internal caller of `set_and_compile`/`get_compiled_classes` across the batcher's speculative block-building paths within the available context, so the exact set of non-committing code paths that can seed the cache (beyond gateway stateful validation) is not exhaustively enumerated here; a full audit of `crates/apollo_batcher` and mempool re-validation flows would be needed to enumerate all seeding vectors.

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

**File:** crates/blockifier/src/state/global_cache.rs (L10-20)
```rust
pub const GLOBAL_CONTRACT_CACHE_SIZE_FOR_TEST: usize = 600;

#[derive(Debug, Clone)]
#[cfg_attr(any(feature = "testing", test), derive(PartialEq))]
pub enum CompiledClasses {
    V0(CompiledClassV0),
    V1(CompiledClassV1, Arc<SierraContractClass>),
    #[cfg(feature = "cairo_native")]
    V1Native(CachedCairoNative),
}
impl CompiledClasses {
```

**File:** crates/blockifier/src/state/contract_class_manager.rs (L21-51)
```rust
    #[derive(Clone)]
    pub struct TrivialClassManager {
        class_cache: RawClassCache,
        compiled_class_hash_v2_cache: GlobalContractCache<CompiledClassHash>,
    }

    // Trivial implementation of the class manager for Native-less projects.
    impl TrivialClassManager {
        pub fn start(config: ContractClassManagerConfig) -> Self {
            assert_eq!(
                config.cairo_native_run_config.cairo_native_mode,
                CairoNativeMode::Off,
                "Trivial class manager does not support native compilation."
            );
            Self {
                class_cache: RawClassCache::new(config.contract_cache_size),
                compiled_class_hash_v2_cache: GlobalContractCache::new(config.contract_cache_size),
            }
        }

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
```

**File:** crates/apollo_state_reader/src/apollo_state.rs (L163-182)
```rust
    fn get_compiled_class_from_db(&self, class_hash: ClassHash) -> StateResult<CompiledClasses> {
        if self.is_declared(class_hash)? {
            // Cairo 1.
            let (casm_compiled_class, sierra) = self.read_casm_and_sierra(class_hash)?;
            let sierra_version = sierra.get_sierra_version()?;
            return Ok(CompiledClasses::V1(
                CompiledClassV1::try_from((casm_compiled_class, sierra_version))?,
                Arc::new(sierra),
            ));
        }

        // Possibly Cairo 0.
        let v0_compiled_class = self.read_deprecated_casm(class_hash)?;
        match v0_compiled_class {
            Some(starknet_api_contract_class) => {
                Ok(CompiledClasses::V0(CompiledClassV0::try_from(starknet_api_contract_class)?))
            }
            None => Err(StateError::UndeclaredClassHash(class_hash)),
        }
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

**File:** crates/blockifier/src/state/state_reader_and_contract_manager_test.rs (L196-240)
```rust
#[cfg(not(feature = "cairo_native"))]
fn cairo_1_cached_scenario() -> GetCompiledClassTestScenario {
    GetCompiledClassTestScenario {
        expectations: GetCompiledClassTestExpectation {
            get_compiled_classes_result: None,
            is_declared_result: Some(Ok(true)), // Verification call for cached Cairo1 class.
        },
        expected_result: Ok(RunnableCompiledClass::test_casm_contract_class()),
    }
}

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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L332-343)
```rust
        // Move state into the blocking task and run CPU-heavy validation.
        let state_reader_and_contract_manager = self.take_state_reader_and_contract_manager();

        let cur_span = Span::current();
        #[allow(clippy::result_large_err)]
        tokio::task::spawn_blocking(move || {
            cur_span.in_scope(|| {
                let state = CachedState::new(state_reader_and_contract_manager);
                let mut blockifier_validator = StatefulValidator::create(state, block_context);
                blockifier_validator.validate(account_tx)
            })
        })
```
