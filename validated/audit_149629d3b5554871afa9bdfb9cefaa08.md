### Title
Contract class cache is not invalidated on `revert_block` in the batcher, causing state divergence via reuse of classes from reverted blocks - (File: `crates/apollo_batcher/src/batcher.rs`, `crates/blockifier/src/state/state_reader_and_contract_manager.rs`)

### Summary
The Kim NFT bug class is: an entity's "completion/removal" is not durably recorded, and the code that decides whether the entity is still valid relies on an incidental proxy (physical possession by the contract) rather than an explicit, always-checked state flag — allowing stale data to be treated as current. The sequencer contains a structural analog in the contract-class cache used by the block execution/state-reading layer: the in-memory `ContractClassManager` cache used by `get_compiled_from_class_manager` is the "possession" proxy, and for Cairo0 (V0) classes there is no re-validation against the current declared-class state at all, while for Cairo1 classes the guard (`is_declared`) exists specifically because "existence in the cache does not guarantee validity ... it might contain a declared class from a reverted block." The sequencer's own code comments confirm that a block revert can leave stale, invalid class entries in this cache and that the fix is to clear/verify on revert — but this invalidation is only performed in the legacy `native_blockifier` python-execution wrapper, not in the actual Rust sequencer `Batcher::revert_block` path.

### Finding Description
`StateReaderAndContractManager::get_compiled_from_class_manager` decides whether to trust a cached compiled class purely based on cache hit, with an explicit but Cairo1-only re-validation: [1](#0-0) 

The comment on this exact code documents the threat model directly: "existence in the cache does not guarantee that (it might contain a declared class from a reverted block, for example)" — but the guard (`is_declared`) is **only applied to non-V0 (Cairo1) classes**; `RunnableCompiledClass::V0(_) => {}` performs no equivalent check: [2](#0-1) 

This is consistent with the `FetchCompiledClasses::is_declared` contract itself, which is documented and implemented to always return `false`/be inapplicable for Cairo0 classes: [3](#0-2) [4](#0-3) 

The correct mitigation — clearing/invalidating the cache whenever a block is reverted — is implemented, but only in the legacy `native_blockifier` (Python-embedded) block executor: [5](#0-4) 

The production Rust sequencer's `Batcher::revert_block` (used by `apollo_batcher`) does not perform this cache invalidation. It aborts in-flight work, reverts the commitment, and reverts storage, but never touches the `ContractClassManager`/class cache: [6](#0-5) 

Consequently, if a block containing a Cairo0 `Declare`/deploy (which populates the shared, process-wide `ContractClassManager` cache via `set_and_compile`) is later reverted (e.g., due to a Starknet OS re-execution mismatch, consensus rollback, or other revert trigger reaching `Batcher::revert_block`), the compiled V0 class remains cached and will continue to be served by `get_compiled_class` for subsequent blocks — with no state-based re-check, because Cairo0 classes are exempt from the `is_declared` guard entirely. This is the direct analog of the marketplace bug: the "listing" (declared-class state) was rolled back/invalidated, but the code path that serves it to new transactions still treats the stale cached artifact as valid because it only checks "does the cache have it" (possession) instead of "is this class currently declared in the canonical state" for V0 classes.

### Impact Explanation
This causes execution using a class definition that is not actually declared in the (reverted-to) canonical state. Any contract deployment or `library_call`/replace-class referencing that class hash after the revert would execute using logic the network's current state says does not exist, producing state transitions/state diffs inconsistent with what an honest node re-deriving state purely from committed storage (without the stale cache) would produce. This is exactly the "wrong committed root or block hash / honest-node divergence" class of impact: a sequencer node with a warm, unflushed cache diverges from a node without that cache (or one that flushed it correctly), leading to a consensus-breaking discrepancy in the resulting state root for subsequent blocks.

### Likelihood Explanation
Reaching this requires (1) a Cairo0 declare/class-usage transaction landing in a block that is subsequently reverted via `Batcher::revert_block`, and (2) a later transaction (from any sender) referencing the same class hash before that cache entry ages out. Block reverts are a normal, expected sequencer operational path (not attacker-controlled directly, but triggerable by conditions such as re-execution/commitment mismatches), and any user (unprivileged) can submit the Cairo0 declare/deploy and the follow-up transaction referencing the class hash — satisfying the "single submitted transaction" reachability requirement for the second half of the scenario, once a revert has occurred through the normal (non-malicious) operational lifecycle of the sequencer node.

### Recommendation
Mirror the invalidation performed in `native_blockifier::py_block_executor::revert_block` inside `apollo_batcher::Batcher::revert_block`: clear (or selectively invalidate the class hashes affected by) the shared `ContractClassManager` cache whenever a block revert occurs. Additionally, remove the Cairo0 exemption in `get_compiled_from_class_manager` by extending `FetchCompiledClasses::is_declared` (or an equivalent check) to cover deprecated (Cairo0) classes as well, so that cache hits for V0 classes are re-validated against current state the same way V1 classes are, closing the gap even if a future revert path is again added without cache invalidation.

### Proof of Concept
1. Node processes block N containing a `Declare` (Cairo0) transaction for class hash `C`; `get_compiled_from_class_manager` caches `C` via `contract_class_manager.set_and_compile`.
2. A condition causes the sequencer to revert block N via `Batcher::revert_block` (`crates/apollo_batcher/src/batcher.rs:1426`), which reverts storage/commitment but never calls `contract_class_manager.clear()`.
3. A new block N (or later) is built; a transaction deploys/uses class hash `C` (e.g., `DeployAccount` or `replace_class`) without a valid on-chain declare for `C` in the reverted state.
4. `get_compiled_class(C)` hits the still-warm cache; since the cached value is `RunnableCompiledClass::V0`, no `is_declared` check is performed, and the stale/no-longer-declared class is returned and executed successfully.
5. The resulting state diff/root reflects execution against class `C`, which honest nodes rebuilding state strictly from the (reverted) committed storage would reject as `UndeclaredClassHash`, producing a state/root divergence.

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

**File:** crates/blockifier/src/test_utils/dict_state_reader.rs (L170-176)
```rust
    fn is_declared(&self, class_hash: ClassHash) -> StateResult<bool> {
        Ok(match self.class_hash_to_class.get(&class_hash) {
            // Cairo0 classes are not declared.
            Some(class) => !matches!(class, RunnableCompiledClass::V0(_)),
            None => false,
        })
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
