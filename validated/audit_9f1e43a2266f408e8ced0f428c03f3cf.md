[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** aptos-move/block-executor/src/code_cache_global_manager.rs (L178-182)
```rust
/// Module cache manager used by Aptos block executor. Ensures that only one thread has exclusive
/// access to it at a time.
pub struct AptosModuleCacheManager {
    inner: Mutex<ModuleCacheManager<ModuleId, CompiledModule, Module, AptosModuleExtension>>,
}
```

**File:** aptos-move/block-executor/src/code_cache_global_manager.rs (L196-222)
```rust
    fn try_lock_inner(
        &self,
        state_view: &impl StateView,
        config: &BlockExecutorModuleCacheLocalConfig,
        transaction_slice_metadata: TransactionSliceMetadata,
    ) -> Result<AptosModuleCacheManagerGuard<'_>, VMStatus> {
        // Get the current environment from storage.
        let storage_environment =
            AptosEnvironment::new_with_delayed_field_optimization_enabled(&state_view);

        Ok(match self.inner.try_lock() {
            Some(mut guard) => {
                guard.check_ready(storage_environment, config, transaction_slice_metadata)?;
                AptosModuleCacheManagerGuard::Guard { guard }
            },
            None => {
                alert_or_println!("Locking module cache manager failed, fallback to empty caches");

                // If this is true, we failed to acquire a lock, and so default storage environment
                // and empty (thread-local) module caches will be used.
                AptosModuleCacheManagerGuard::None {
                    environment: storage_environment,
                    module_cache: GlobalModuleCache::empty(),
                }
            },
        })
    }
```

**File:** aptos-move/block-executor/src/code_cache_global_manager.rs (L802-823)
```rust
    #[test]
    fn test_too_many_interned_tys_flushes_cache() {
        let (num_interned_tys_before, num_interned_ty_vecs_before, mut manager) =
            cache_manager_for_test();
        let state_view = MockStateView::empty();
        let metadata_2 = TransactionSliceMetadata::block_from_u64(1, 2);

        assert_ok!(manager.check_ready(
            AptosEnvironment::new(&state_view),
            &BlockExecutorModuleCacheLocalConfig {
                prefetch_framework_code: false,
                max_interned_tys: 2,
                ..Default::default()
            },
            metadata_2
        ));
        assert_caches_empty(
            &manager,
            num_interned_tys_before,
            num_interned_ty_vecs_before,
        );
    }
```

**File:** third_party/move/move-vm/types/src/ty_interner.rs (L93-108)
```rust
impl TypeInterner {
    fn intern(&self, repr: TypeRepr) -> TypeId {
        if let Some(id) = self.inner.read().interned.get(&repr) {
            return *id;
        }

        let mut inner = self.inner.write();
        if let Some(id) = inner.interned.get(&repr) {
            return *id;
        }

        let id = TypeId(inner.data.len() as u32);
        inner.data.push(repr);
        inner.interned.insert(repr, id);
        id
    }
```

**File:** third_party/move/move-vm/types/src/ty_interner.rs (L199-213)
```rust
    pub fn flush(&self) {
        self.flush_impl();
        self.warmup();
    }

    /// Flushes all cached data without warming up the cache.
    fn flush_impl(&self) {
        let mut ty_interner = self.ty_interner.inner.write();
        ty_interner.clear();
        drop(ty_interner);

        let mut ty_vec_interner = self.ty_vec_interner.inner.write();
        ty_vec_interner.clear();
        drop(ty_vec_interner);
    }
```
