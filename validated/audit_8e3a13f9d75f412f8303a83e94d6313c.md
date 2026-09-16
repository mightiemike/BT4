### Title
Untrusted Cairo0 class-cache entries from discarded/uncommitted proposals can be executed as "declared" without verification against committed state - (File: crates/blockifier/src/state/state_reader_and_contract_manager.rs)

### Summary
`StateReaderAndContractManager::get_compiled_from_class_manager` resolves a class hash from the shared, cross-height `ContractClassManager` global cache and, for Cairo0 (`RunnableCompiledClass::V0`) entries, returns the cached class with **no verification** that the class is actually declared in the committed chain state. This "resolve-before-trust" pattern is the exact bug class described in the reference advisory (an entity is resolved and used before its trust/authorization is actually verified). Because this cache is shared by the batcher across block *proposal*, *validation*, and *view-call* flows — and across multiple heights/rounds — and is only explicitly cleared on an actual storage `revert_block`, a Cairo0 class that is compiled and cached while executing a declare transaction inside a proposal that never gets committed (e.g., a losing/aborted consensus round) remains "trusted" in the process's cache for subsequent, unrelated proposals.

### Finding Description
`get_compiled_from_class_manager` in [1](#0-0)  checks cache hits like this:

```rust
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
```

The comment explicitly acknowledges that cache presence does not imply actual on-chain declaration (e.g. after a reverted block), and a guard (`is_declared`) is added for Cairo1/native classes — but the same guard is deliberately *skipped* for Cairo0 (`V0`) classes. This is confirmed by the accompanying test suite comment: `is_declared` only reads the Cairo1 declared-classes table, and the Cairo0 route "never reads storage" [2](#0-1) .

The underlying `ContractClassManager`/`RawClassCache` is a single, long-lived, process-wide cache that the batcher explicitly shares between block *production* and *view calls*: "Block production and view calls share one class cache." [3](#0-2) . The same `contract_class_manager` instance is threaded into the `BlockBuilderFactory` used for every proposal (`propose_block`/`validate_block`) at every height [4](#0-3) , so cache entries populated while speculatively executing one candidate block persist into unrelated later proposals/heights on the same node.

The cache is only explicitly cleared on an actual `revert_block` (storage rollback of a previously *committed* block): "Clear global class cache, to properly revert classes declared in the reverted block." [5](#0-4) . There is no equivalent clearing when a *proposal* (never committed to storage) is discarded, superseded by another proposer, or fails during a consensus round — the class-cache population path (`set_and_compile`) is invoked unconditionally whenever a class is fetched during transaction execution [6](#0-5) , regardless of whether that block/proposal is ever finalized.

Consequently: a Cairo0 declare transaction executed while building/validating a candidate proposal poisons the shared cache with an entry for that `ClassHash` mapped to `CompiledClasses::V0`. If that proposal is discarded (round change, competing proposal wins, validation for a losing fork, etc.), the class is never recorded as declared in committed storage (`deprecated_declared_classes` table is untouched). Yet any later proposal/validation/view-call on the same node that requests `get_compiled_class` for that same class hash gets a cache hit and, because the `V0` branch performs no `is_declared` check, treats the class as declared and returns it as runnable — with zero comparison against the actual committed state.

### Impact Explanation
This breaks the state-machine invariant that a class must be declared (and paid for) in committed state before it can be used by a `deploy`/`replace_class`/direct-call. An attacker can craft a Cairo0 declare transaction, get it included/executed in a losing or otherwise-discarded proposal candidate on a target sequencer node (achievable because gateway/mempool inclusion into a proposal attempt does not require eventual commitment), and then, in a subsequent legitimate block from the same node, deploy a contract or invoke a class hash that was never actually declared in canonical state. The resulting block's state diff (`address_to_class_hash`) would reference a class hash with no corresponding declaration in the chain history. Honest nodes re-executing/verifying that block (Starknet OS re-execution, other sequencer state readers, sync nodes without the poisoned cache) will not find the class declared and will diverge from — or be unable to reproduce — the state root/block hash produced by the poisoned node, i.e. honest-node divergence / wrong committed state root, potentially halting confirmation of new blocks built atop this inconsistency.

### Likelihood Explanation
Reachable purely from a single, unprivileged, ordinary declare transaction (Cairo0/`DeclareTransaction` version 0/1) submitted by any account — no special privileges, no operator/proposer collusion required. The only additional condition is that the transaction's containing proposal is not the one ultimately committed (a routine, frequent occurrence in a BFT round-based consensus protocol under normal network conditions, e.g. round changes, competing proposals, timeouts), which is squarely within a single sender's control to arrange by resubmitting.

### Recommendation
Apply the same `is_declared` verification to Cairo0 (`RunnableCompiledClass::V0`) cache hits as is already applied to Cairo1/native classes in `get_compiled_from_class_manager`, removing the `RunnableCompiledClass::V0(_) => {}` no-op branch. Additionally, ensure the shared `ContractClassManager` cache cannot leak class entries from discarded/uncommitted proposals into unrelated later proposals — e.g., by invalidating/removing entries populated during an aborted proposal, or by always validating cache hits (for all class versions) against authoritative declared-class state before use.

### Proof of Concept
1. Node N is running as both proposer/validator with one shared `ContractClassManager` across proposal rounds (`crates/apollo_batcher/src/batcher.rs` `create_batcher`).
2. Attacker submits a valid Cairo0 `Declare` transaction for class `C` (never before declared) into the mempool.
3. Node N includes it while building/validating some proposal `P1` at height `H`; `get_compiled_from_class_manager` executes `get_compiled_classes` → `set_and_compile(C, CompiledClasses::V0(..))`, populating the shared cache.
4. Consensus round for `H` changes / `P1` is not the finalized block (e.g., another proposer's block wins, or `P1` fails deadline); class `C` is never recorded in committed `deprecated_declared_classes`.
5. In a later height `H' > H` (or a retried round of `H`), attacker submits a `DeployAccount`/`Invoke` referencing class hash `C` directly (skipping declare). On node N, `get_compiled_from_class_manager` gets a cache hit for `C` (`V0` branch), skips `is_declared`, and returns the cached class as runnable, allowing the deploy/call to succeed and be included in a committed block, even though `C` was never declared in canonical state.
6. Any honest node without the poisoned cache re-executing/validating this block will fail to resolve class `C` (`UndeclaredClassHash`), producing a different result/state root than node N — consensus/state divergence.

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

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L88-101)
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
```

**File:** crates/apollo_batcher/src/batcher_test.rs (L2463-2467)
```rust
/// Pins the route a declared Cairo 0 class takes. `is_declared` reads the Cairo 1 declared classes
/// table only, and `append_state_diff` writes `deprecated_declared_classes` to a different table,
/// so the class takes the deprecated route, which asks the class manager for the definition and
/// never reads storage. Were `is_declared` widened to the deprecated table, the class would take
/// the Cairo 1 route instead and panic in `ClassReader::read_casm`.
```

**File:** crates/apollo_batcher/src/batcher.rs (L1747-1753)
```rust
    // Block production and view calls share one class cache.
    let contract_class_manager =
        ContractClassManager::start(config.static_config.contract_class_manager_config.clone());
    let block_builder_factory = Box::new(BlockBuilderFactory {
        block_builder_config: config.static_config.block_builder_config.clone(),
        storage_reader: storage_reader.clone(),
        contract_class_manager: contract_class_manager.clone(),
```

**File:** crates/apollo_batcher/src/block_builder.rs (L761-811)
```rust
impl BlockBuilderFactory {
    // TODO(noamsp): Investigate and remove this clippy warning.
    fn preprocess_and_create_transaction_executor(
        &self,
        block_metadata: BlockMetadata,
        native_classes_whitelist: NativeClassesWhitelist,
        runtime: tokio::runtime::Handle,
    ) -> BlockBuilderResult<ConcurrentTransactionExecutor<ApolloStateReaderAndContractManager>>
    {
        info!(
            "preprocess and create transaction executor for block {}",
            block_metadata.block_info.block_number
        );
        let height = block_metadata.block_info.block_number;
        let block_builder_config = self.block_builder_config.clone();
        let versioned_constants = VersionedConstants::get_versioned_constants(
            block_builder_config.versioned_constants_overrides,
        );
        let block_context = BlockContext::new(
            block_metadata.block_info,
            block_builder_config.chain_info,
            versioned_constants,
            block_builder_config.bouncer_config,
        );

        // Block production has no per-call deadline to bound this against; leave it unbounded, as
        // before.
        let class_reader = Some(ClassReader {
            reader: self.class_manager_client.clone(),
            runtime,
            deadline: None,
        });
        let apollo_reader =
            ApolloReader::new_with_class_reader(self.storage_reader.clone(), height, class_reader);
        let state_reader = StateReaderAndContractManager::new_with_native_classes_whitelist(
            apollo_reader,
            self.contract_class_manager.clone(),
            native_classes_whitelist,
            Some(BATCHER_CLASS_CACHE_METRICS),
        );

        let executor = ConcurrentTransactionExecutor::start_block(
            state_reader,
            block_context,
            block_metadata.retrospective_block_hash,
            self.worker_pool.clone(),
            None,
        )?;

        Ok(executor)
    }
```

**File:** crates/native_blockifier/src/py_block_executor.rs (L322-330)
```rust
    /// Atomically reverts block header and state diff of given block number.
    /// If header exists without a state diff (usually the case), only the header is reverted.
    /// (this is true for every partial existence of information at tables).
    #[pyo3(signature = (block_number))]
    pub fn revert_block(&mut self, block_number: u64) -> NativeBlockifierResult<()> {
        // Clear global class cache, to properly revert classes declared in the reverted block.
        self.contract_class_manager.clear();
        self.storage.revert_block(block_number)
    }
```
