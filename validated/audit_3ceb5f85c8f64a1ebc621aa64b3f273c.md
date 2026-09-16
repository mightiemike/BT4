## Title
Global contract-class cache is not invalidated on block revert in `apollo_batcher::Batcher::revert_block`, allowing stale/undeclared classes to be executed post-revert - (File: `crates/apollo_batcher/src/batcher.rs`)

## Summary
`Batcher::revert_block` reverts the persistent storage (declared classes, state diffs, markers) but never clears the shared `ContractClassManager` global class cache that backs `StateReaderAndContractManager::get_compiled_class`. The class-lookup guard in `get_compiled_from_class_manager` re-validates a cached class against current storage (`is_declared`) only for Cairo1 classes, and explicitly skips this check for Cairo0 (`V0`) classes. Combined with the missing cache invalidation on revert, this reproduces the reported bug class exactly: a "guard" that is supposed to gate access to state based on a persisted fact is bypassed because a volatile in-memory cache is trusted without being kept consistent with the ground-truth store, and the check is not applied uniformly across all code paths.

## Finding Description
`StateReaderAndContractManager::get_compiled_from_class_manager` implements the cache lookup guard: [1](#0-0) 

For Cairo1 classes it explicitly re-checks `self.state_reader.is_declared(class_hash)` because, per the code's own comment, "existence in the cache does not guarantee [declaration], since it might contain a declared class from a reverted block." For Cairo0 (`V0`) classes, **no such re-validation is performed at all** — the cached class is returned unconditionally on a cache hit.

This global cache (`ContractClassManager` / `RawClassCache` / `GlobalContractCache`) is shared and long-lived across block executions: [2](#0-1) 

The `native_blockifier` Python-bridge revert path is aware that a block revert can strand stale entries in this cache and explicitly clears it before reverting storage: [3](#0-2) 

However, the production Rust sequencer's `Batcher::revert_block` — the code path actually used by the sequencer/consensus-manager to revert a locally-built or synced block — does **not** clear the contract class cache. It only reverts storage: [4](#0-3) 

The storage-level revert removes the class's declaration facts (`declared_classes`, `deprecated_declared_classes`, markers) from persistent state: [5](#0-4) 

and this is confirmed to change `is_declared`/class-lookup results after a revert in the storage test suite: [6](#0-5) 

Putting these together: on a sequencer node that (a) executed a class-declaring transaction in a block that is later reverted (e.g., via consensus/state-sync revert), and (b) still holds that class warm in its process-lifetime `ContractClassManager` cache, a subsequent transaction referencing that (now formally undeclared) Cairo0 class hash will still succeed via the cache-hit path, because the guard that exists for Cairo1 classes is both (i) absent for Cairo0 classes, and (ii) moot anyway since the cache was never invalidated on revert.

## Impact Explanation
This directly causes **honest-node divergence** in transaction execution outcome and consequently in the resulting state diff / committed state root for the same block height:
- A sequencer node that had the class warm in cache (e.g., the original proposer that built/executed the now-reverted block, or any node that previously executed a call against that class hash) will accept and successfully execute a transaction that uses the class hash, deriving one state diff.
- A node that restarted, was freshly synced, or otherwise never warmed the class into its in-process cache will correctly consult storage, find the class undeclared, and reject the transaction with `StateError::UndeclaredClassHash`.

This is a state-transition-function nondeterminism bug: identical inputs (transactions, prior committed state) produce different execution results (success vs. rejection) depending purely on process-local cache history, which can lead to a wrongly committed state root/block hash on some validators and a network unable to reach or verify consensus on the correct state, or acceptance of an unauthorized/invalid execution (e.g. running code for a contract class that is not currently declared in the canonical state). This satisfies the "honest-node divergence" / "wrong committed root" criteria for High severity.

## Likelihood Explanation
Reverts are a real production pathway (consensus reverts, state-sync reverts) exercised through `ConsensusManager::revert_batcher_blocks` → `BatcherClient::revert_block` → `Batcher::revert_block`, as shown by the existing test harness: [7](#0-6) 

Any transaction (declare-then-revert, or a normal invoke/deploy referencing a class hash that was declared in a since-reverted block) submitted by an ordinary, unprivileged sender can trigger the divergence path — no special privileges, malicious operator, or p2p manipulation is required beyond the network experiencing a normal revert (which the protocol itself supports and exercises). The bug requires no attacker-controlled cache poisoning beyond naturally-occurring warm caches from prior execution, making it reachable purely through ordinary declare/invoke/deploy transaction submission combined with the sequencer's own revert mechanism.

## Recommendation
1. In `Batcher::revert_block` (`crates/apollo_batcher/src/batcher.rs`), clear the shared `ContractClassManager`/global contract class cache before or as part of reverting storage, mirroring what `native_blockifier`'s `PyBlockExecutor::revert_block` already does.
2. Remove the special-cased skip for `RunnableCompiledClass::V0` in `get_compiled_from_class_manager` (`crates/blockifier/src/state/state_reader_and_contract_manager.rs`) and apply the same `is_declared` re-validation uniformly to all cached class variants, not only Cairo1.
3. Consider tying the cache's validity to a monotonic state marker/version so that any storage revert automatically invalidates affected cache entries, rather than relying on manual `clear()` calls scattered across different execution entry points.

## Proof of Concept
Conceptual reproduction (illustrating the divergence, not runnable code):
1. Node A (proposer) executes block N containing a `declare` for a Cairo0 class `C` with hash `H`. `StateReaderAndContractManager::get_compiled_from_class_manager` caches `C` in the process-global `ContractClassManager`.
2. Consensus reverts block N (e.g., due to a competing proposal or state-sync correction) via `ConsensusManager::revert_batcher_blocks` → `Batcher::revert_block`, which calls `storage_writer.revert_block(height)`, removing `H` from `declared_classes`/`deprecated_declared_classes` in storage but leaving the `ContractClassManager` cache on Node A untouched (`crates/apollo_batcher/src/batcher.rs:1426-1457`).
3. A user submits a new transaction (invoke/deploy) referencing class hash `H`, without re-declaring it.
4. On Node A: `get_compiled_from_class_manager` hits the stale cache entry for `H`; since it is `RunnableCompiledClass::V0`, no `is_declared` check is performed (`crates/blockifier/src/state/state_reader_and_contract_manager.rs:73-74`), so the transaction executes successfully.
5. On Node B (freshly synced, cold cache): the same lookup misses the cache, queries storage, finds `H` undeclared, and returns `StateError::UndeclaredClassHash`, rejecting the transaction.
6. Nodes A and B now disagree on whether the transaction is valid and on the resulting state diff/root for the block containing it — an honest-node execution divergence.

### Citations

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L66-87)
```rust
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

**File:** crates/starknet_api/src/class_cache.rs (L12-37)
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

**File:** crates/apollo_storage/src/state/mod.rs (L686-754)
```rust
    #[latency_histogram("storage_revert_state_diff_latency_seconds", false)]
    fn revert_state_diff(
        self,
        block_number: BlockNumber,
    ) -> StorageResult<(Self, Option<RevertedStateDiff>)> {
        let markers_table = self.open_table(&self.tables.markers)?;
        let declared_classes_table = self.open_table(&self.tables.declared_classes)?;
        let declared_classes_block_table = self.open_table(&self.tables.declared_classes_block)?;
        let deprecated_declared_classes_table =
            self.open_table(&self.tables.deprecated_declared_classes)?;
        let deprecated_declared_classes_block_table =
            self.open_table(&self.tables.deprecated_declared_classes_block)?;
        // TODO(yair): Consider reverting the compiled classes in their own module.
        let compiled_classes_table = self.open_table(&self.tables.casms)?;
        let compiled_class_hash_v2_table =
            self.open_table(&self.tables.stateless_compiled_class_hash_v2)?;
        let deployed_contracts_table = self.open_table(&self.tables.deployed_contracts)?;
        let nonces_table = self.open_table(&self.tables.nonces)?;
        let storage_table = self.open_table(&self.tables.contract_storage)?;
        let state_diffs_table = self.open_table(&self.tables.state_diffs)?;
        let compiled_class_hash_table = self.open_table(&self.tables.compiled_class_hash)?;

        let current_state_marker = self.get_state_marker()?;

        // Reverts only the last state diff.
        let Some(next_block_number) = block_number
            .next()
            .filter(|next_block_number| *next_block_number == current_state_marker)
        else {
            debug!(
                "Attempt to revert a non-existing / old state diff of block {}. Returning without \
                 an action.",
                block_number
            );
            return Ok((self, None));
        };

        let thin_state_diff = self
            .get_state_diff(block_number)?
            .unwrap_or_else(|| panic!("Missing state diff for block {block_number}."));
        markers_table.upsert(&self.txn, &MarkerKind::State, &block_number)?;
        let classes_marker = markers_table.get(&self.txn, &MarkerKind::Class)?.unwrap_or_default();
        if classes_marker == next_block_number {
            markers_table.upsert(&self.txn, &MarkerKind::Class, &block_number)?;
        }
        let compiled_classes_marker =
            markers_table.get(&self.txn, &MarkerKind::CompiledClass)?.unwrap_or_default();
        if compiled_classes_marker == next_block_number {
            markers_table.upsert(&self.txn, &MarkerKind::CompiledClass, &block_number)?;
        }
        let deleted_class_hashes = delete_declared_classes_block(
            &self.txn,
            &thin_state_diff,
            &declared_classes_block_table,
            block_number,
        )?;
        let deleted_classes = delete_declared_classes(
            &self.txn,
            &thin_state_diff,
            &declared_classes_table,
            &self.file_handlers,
        )?;
        let deleted_deprecated_class_hashes = delete_deprecated_declared_classes_block(
            &self.txn,
            block_number,
            &thin_state_diff,
            &deprecated_declared_classes_block_table,
        )?;
        let deleted_deprecated_classes = delete_deprecated_declared_classes(
```

**File:** crates/apollo_storage/src/state/state_test.rs (L797-808)
```rust
    // Revert the block and assert that the classes are no longer declared.
    let (txn, _) = writer.begin_rw_txn().unwrap().revert_state_diff(BlockNumber(0)).unwrap();
    txn.commit().unwrap();
    let txn = reader.begin_ro_txn().unwrap();
    let state_reader = txn.get_state_reader().unwrap();
    assert!(state_reader.get_class_definition_at(state_number, &class_hash).unwrap().is_none());
    assert!(
        state_reader
            .get_deprecated_class_definition_at(state_number, &deprecated_class_hash)
            .unwrap()
            .is_none()
    );
```

**File:** crates/apollo_consensus_manager/src/consensus_manager.rs (L336-372)
```rust
    // Performs reverts to the batcher.
    async fn revert_batcher_blocks(&self, revert_up_to_and_including: BlockNumber) {
        // If we revert all blocks up to height X (including), the new height marker will be X.
        let batcher_height_marker = self
            .batcher_client
            .get_height()
            .await
            .expect("Failed to get batcher_height_marker from batcher")
            .height;

        // Ensure voted height storage is reverted, even if no batcher revert is needed.
        // This allows consensus to proceed at the target height after restart.
        self.voted_height_storage
            .lock()
            .await
            .revert_height(revert_up_to_and_including)
            .expect("Failed to revert height in the consensus manager's voted height storage");

        // This function will panic if the revert fails.
        let revert_blocks_fn = move |height| async move {
            self.batcher_client.revert_block(RevertBlockInput { height }).await.unwrap_or_else(
                |err| panic!("Failed to revert block at height {height} in the batcher: {err:?}"),
            );
        };

        const BATCHER_REVERT_COMPONENT_DATA: RevertComponentData = RevertComponentData {
            name: "Batcher",
            revert_metric: CONSENSUS_REVERTED_BATCHER_UP_TO_AND_INCLUDING,
        };
        revert_blocks_and_eternal_pending(
            batcher_height_marker,
            revert_up_to_and_including,
            revert_blocks_fn,
            &BATCHER_REVERT_COMPONENT_DATA,
        )
        .await;
    }
```
