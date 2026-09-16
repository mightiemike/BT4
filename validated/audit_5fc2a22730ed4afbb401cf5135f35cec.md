## Finding

### Title
Global contract-class cache is not invalidated on block revert for Cairo0 (V0) classes, allowing execution of undeclared classes and honest-node divergence - (File: crates/blockifier/src/state/state_reader_and_contract_manager.rs)

### Summary
`StateReaderAndContractManager::get_compiled_from_class_manager` skips the "is the class still declared" check for Cairo0 (`RunnableCompiledClass::V0`) entries returned by the process-wide `ContractClassManager` / `GlobalContractCache`, while explicitly performing that check for Cairo1 classes. Because the cache is a long-lived, process-global structure (`starknet_api::class_cache::GlobalContractCache`) that is not scoped to a particular block/state, a class hash that was declared and then reverted (e.g., due to a reorg/revert) can remain cached and be served as "compiled" even though it is no longer declared in the canonical state.

### Finding Description
`get_compiled_from_class_manager` reads from the shared cache first: [1](#0-0) 

For non-V0 classes it explicitly guards against stale cache entries left over from a reverted block by consulting `is_declared`, with a comment acknowledging the exact bug class described in the CVE ("it might contain a declared class from a reverted block"). For `RunnableCompiledClass::V0` no equivalent guard exists — the branch is empty (`RunnableCompiledClass::V0(_) => {}`).

This asymmetry is not accidental cosmetics: `is_declared` is documented and implemented to only track Cairo1 declarations, always returning `false` for Cairo0: [2](#0-1) [3](#0-2) 

So there is no mechanism at all to detect that a cached V0 class has since become "undeclared" due to a revert.

The cache itself is a simple LRU keyed only by `ClassHash`, with no block/version tag and no automatic invalidation tied to state changes: [4](#0-3) 

The developers were aware that reverts require clearing this cache: the Python-binding integration (`native_blockifier`) explicitly clears the whole class cache on revert: [5](#0-4) 

However, the production sequencer's block-revert paths used by `apollo_batcher` and `apollo_reverts` only revert on-disk storage state (headers, state diffs, declared/deprecated-declared-class tables, class-manager markers) — they do not clear the in-process `ContractClassManager`/`GlobalContractCache` used by the blockifier during execution: [6](#0-5) [7](#0-6) [8](#0-7) 

As a result, on a node whose `ContractClassManager` warm cache already contains a V0 class that gets reverted (declared class becomes officially undeclared in storage), subsequent transactions that reference that class hash (e.g., `deploy`, `library_call`, or setting `class_hash_at`) will still succeed via `get_compiled_from_class_manager`'s cache hit path, since no `is_declared` check exists for V0. A node whose cache does not contain the class (cold cache, e.g., after a fresh sync or process restart) will instead correctly return `StateError::UndeclaredClassHash` when it fetches from `get_compiled_classes`/storage, because storage correctly reflects the revert (see the revert tests explicitly asserting that a class definition becomes `None` after revert): [9](#0-8) 

This produces divergent execution outcomes for the identical transaction and identical committed state, between a node with a warm class cache and one without.

### Impact Explanation
This is a direct analog of CVE-2018-16862's bug class: data belonging to a deleted/invalidated entity (here, a reverted class declaration) is served from a cache that was never invalidated on deletion, and reused as if it were still valid current state. In this sequencer, the consequence is not leaked file contents but **honest-node divergence**: identical transactions against identical (reverted-to) state can execute successfully on some nodes and fail with `UndeclaredClassHash` on others, since the cache is keyed only by `ClassHash` and never invalidated for V0 classes on revert. Divergent execution of a transaction affecting state changes (deploys, `replace_class`, storage writes performed by a constructor) leads to differing state diffs / state roots across nodes, i.e., a wrong committed root / block hash relative to what other honest nodes compute, and potentially a network unable to reach consensus on the block.

### Likelihood Explanation
Triggering requires a chain reorg/revert of a block that declared a Cairo0 class, followed by re-use of that class hash while some nodes still have it warm in their in-process cache and others do not. Reverts are a normal (if infrequent) operational event handled explicitly by `apollo_reverts`/`apollo_batcher`/`apollo_committer`, and the surrounding code (`native_blockifier`) shows the developers are already aware reverts must clear this exact cache — but that fix was applied only to the Python-binding integration path, not to the current Rust sequencer's revert flow. Any attacker or sequence of transactions that causes a declare→revert→reuse pattern (which is entirely reachable by a normal transaction sender declaring a class, followed by an operator-independent reorg of a small depth) can trigger the divergence; no privileged access is required to exploit the resulting inconsistency once a revert occurs.

### Recommendation
- Add symmetry to `get_compiled_from_class_manager`: perform an equivalent "is this V0 class still declared" check before trusting a cache hit, analogous to the V1 branch, instead of skipping the check entirely for `RunnableCompiledClass::V0`.
- Alternatively/additionally, invalidate (clear or selectively evict) the `ContractClassManager`/`GlobalContractCache` whenever `apollo_reverts::revert_block` / `apollo_batcher::revert_block` reverts a block that declared any classes, mirroring the mitigation already present in `native_blockifier::py_block_executor::revert_block`.
- Ensure `is_declared` (or an equivalent primitive) can answer the question for Cairo0 classes as well, rather than being hardcoded to always return `false` for them.

### Proof of Concept
1. Node A declares Cairo0 class `C` in block `N` and executes a transaction that calls `get_compiled_class(C)`, causing `C` to be cached in the process-wide `GlobalContractCache` via `get_compiled_from_class_manager`'s cache-miss path (crates/blockifier/src/state/state_reader_and_contract_manager.rs:88-101).
2. Block `N` is reverted through the standard sequencer revert flow (`apollo_reverts::revert_block` / `apollo_batcher::batcher::revert_block`), which reverts on-disk declared-class tables (crates/apollo_storage/src/state/mod.rs:1006-1046) but does **not** call any equivalent of `contract_class_manager.clear()`.
3. A new transaction (e.g., `deploy` syscall) references class hash `C`, which is now officially undeclared in storage.
4. On Node A (warm cache), `get_compiled_from_class_manager` hits the cache, takes the `RunnableCompiledClass::V0(_) => {}` branch (no declared check), and succeeds — the deploy/library_call proceeds.
5. On Node B, which never cached `C` (cold cache or restarted process), the same call falls through to `get_compiled_classes` → storage, which now returns `UndeclaredClassHash` per the revert semantics verified in `crates/apollo_storage/src/state/state_test.rs:883-895` — the transaction is rejected.
6. Nodes A and B compute different execution results/state diffs for the identical block, causing a state-root/block-hash mismatch.

Note: I could not find within the indexed codebase a call site in the current Rust-native sequencer's production revert path (`apollo_reverts`, `apollo_batcher`, `apollo_committer`) that clears `ContractClassManager`/`GlobalContractCache`; only the `native_blockifier` (Python integration) path does so. This absence is central to the finding, but if such invalidation exists elsewhere and was not indexed, it would mitigate this issue — I recommend a Devin session with full repository access to grep exhaustively for any other cache-invalidation-on-revert hook before finalizing severity.

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

**File:** crates/native_blockifier/src/py_block_executor.rs (L322-329)
```rust
    /// Atomically reverts block header and state diff of given block number.
    /// If header exists without a state diff (usually the case), only the header is reverted.
    /// (this is true for every partial existence of information at tables).
    #[pyo3(signature = (block_number))]
    pub fn revert_block(&mut self, block_number: u64) -> NativeBlockifierResult<()> {
        // Clear global class cache, to properly revert classes declared in the reverted block.
        self.contract_class_manager.clear();
        self.storage.revert_block(block_number)
```

**File:** crates/apollo_reverts/src/lib.rs (L122-153)
```rust
/// Reverts everything related to the block, will succeed even if there is partial information for
/// the block.
// This function will panic if the storage reader fails to revert.
pub fn revert_block(storage_writer: &mut StorageWriter, target_block_marker: BlockNumber) {
    let txn = storage_writer
        .begin_rw_txn()
        .unwrap()
        .revert_header(target_block_marker)
        .unwrap()
        .0
        .revert_body(target_block_marker)
        .unwrap()
        .0
        .revert_state_diff(target_block_marker)
        .unwrap()
        .0
        .try_revert_class_manager_marker(target_block_marker)
        .unwrap()
        .try_revert_base_layer_marker(target_block_marker)
        .unwrap()
        .revert_partial_block_hash_components(&target_block_marker)
        .unwrap()
        .revert_block_hash(&target_block_marker)
        .unwrap()
        .revert_global_root(&target_block_marker)
        .unwrap();

    #[cfg(feature = "os_input")]
    let txn = txn.revert_accessed_keys(target_block_marker).unwrap();

    txn.commit().unwrap();
}
```

**File:** crates/apollo_batcher/src/batcher.rs (L1426-1457)
```rust
    #[instrument(skip(self), err)]
    // This function will panic if there is a storage failure to revert the block.
    pub async fn revert_block(&mut self, input: RevertBlockInput) -> BatcherResult<()> {
        info!("Reverting block at height {}.", input.height);
        let height = self.get_height_from_storage()?.prev().ok_or(
            BatcherError::StorageHeightMarkerMismatch {
                marker_height: BlockNumber(0),
                requested_height: input.height,
            },
        )?;

        if height != input.height {
            return Err(BatcherError::StorageHeightMarkerMismatch {
                marker_height: height.unchecked_next(),
                requested_height: input.height,
            });
        }

        if let Some(height) = self.active_height {
            info!("Aborting all work on height {} due to a revert request.", height);
            self.abort_active_height().await;
        }

        // Wait for the revert commitment to be completed before reverting the storage.
        self.revert_commitment(height).await;

        self.storage_writer.revert_block(height);
        BUILDING_HEIGHT.decrement(1);
        GLOBAL_ROOT_HEIGHT.decrement(1);
        REVERTED_BLOCKS.increment(1);
        Ok(())
    }
```

**File:** crates/apollo_storage/src/state/mod.rs (L1006-1046)
```rust
fn delete_deprecated_declared_classes<'env>(
    txn: &'env DbTransaction<'env, RW>,
    block_number: BlockNumber,
    thin_state_diff: &ThinStateDiff,
    deprecated_declared_classes_table: &'env DeprecatedDeclaredClassesTable<'env>,
    file_handlers: &FileHandlers<RW>,
) -> StorageResult<IndexMap<ClassHash, DeprecatedContractClass>> {
    // Class hashes of the contracts that were deployed in this block.
    let deployed_contracts_class_hashes = thin_state_diff.deployed_contracts.values();

    // Merge the class hashes from the state diff and from the deployed contracts into a single
    // unique set.
    let class_hashes: HashSet<&ClassHash> = thin_state_diff
        .deprecated_declared_classes
        .iter()
        .chain(deployed_contracts_class_hashes)
        .collect();

    let mut deleted_data = IndexMap::new();
    for class_hash in class_hashes {
        // If the class is not in the deprecated classes table, it means that either we didn't
        // download it yet or the hash is of a deployed contract of a new class type. We've decided
        // to avoid deleting these classes because they're from at most 0.11.
        if let Some(IndexedDeprecatedContractClass {
            block_number: declared_block_number,
            location_in_file,
        }) = deprecated_declared_classes_table.get(txn, class_hash)?
        {
            // If the class was declared in a different block then we should'nt delete it.
            if block_number == declared_block_number {
                deleted_data.insert(
                    *class_hash,
                    file_handlers.get_deprecated_contract_class_unchecked(location_in_file)?,
                );
                deprecated_declared_classes_table.delete(txn, class_hash)?;
            }
        }
    }

    Ok(deleted_data)
}
```

**File:** crates/apollo_storage/src/state/state_test.rs (L883-895)
```rust
    // Revert the block and assert that the classes are no longer declared.
    let (txn, _) = writer.begin_rw_txn().unwrap().revert_state_diff(BlockNumber(0)).unwrap();
    txn.commit().unwrap();
    let txn = reader.begin_ro_txn().unwrap();
    let state_reader = txn.get_state_reader().unwrap();
    assert!(state_reader.get_class_definition_block_number(&class_hash).unwrap().is_none());
    assert!(
        state_reader
            .get_deprecated_class_definition_block_number(&deprecated_class_hash)
            .unwrap()
            .is_none()
    );

```
