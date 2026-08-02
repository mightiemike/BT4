No vulnerability found for this question.

**Analysis:** The premise of the question—a "race" between a deferred load from `data_cache` and a same-transaction `MoveTo`—does not correspond to how the Move VM interpreter actually executes. Instruction dispatch inside a single transaction is fully synchronous and single-threaded: each `Instruction` (including `Instruction::MoveTo` and `Instruction::MutBorrowGlobal`/`ImmBorrowGlobal`) is executed to completion, one after another, in the same thread, via the main dispatch loop. [1](#0-0) 

`MoveTo` calls `Interpreter::move_to`, which calls `load_resource_mut` (getting/creating the cached `GlobalValue` entry in `TransactionDataCache`) and then `gv.move_to(resource)`, mutating that same in-memory entry in place. [2](#0-1) 

`BorrowGlobal` calls `Interpreter::borrow_global`, which also calls `load_resource`/`load_resource_mut`, ultimately routed through `MoveVmDataCacheAdapter::load_resource_mut`. [3](#0-2) 

That adapter checks `self.data_cache.contains_resource(addr, ty)` — if the entry already exists in the per-transaction `TransactionDataCache::account_map` (which it does, since `MoveTo` just inserted/updated it), it skips re-fetching from the remote resolver and returns a mutable reference to the *same* `DataCacheEntry.value` object. [4](#0-3) 

`get_resource_mut` retrieves the entry from the same `BTreeMap<AccountAddress, BTreeMap<Type, DataCacheEntry>>` that `MoveTo` just wrote to — there is no separate "loaded snapshot" that could be stale. [5](#0-4) 

Since `TransactionDataCache` is scoped to a single transaction and mutated strictly sequentially by opcode dispatch, there is no concurrency, no deferred/async load, and no mechanism for a `BorrowGlobal` following a `MoveTo` to observe anything other than the just-written value. Any computation built on the borrowed reference after `MoveTo` in the same transaction correctly reflects the write. This is not a race condition — it's ordinary single-threaded, in-order execution — so the described exploit path does not exist in this codebase.

### Citations

**File:** third_party/move/move-vm/runtime/src/interpreter.rs (L1424-1457)
```rust
    /// BorrowGlobal (mutable and not) opcode.
    fn borrow_global(
        &mut self,
        is_mut: bool,
        is_generic: bool,
        data_cache: &mut impl MoveVmDataCache,
        gas_meter: &mut impl GasMeter,
        traversal_context: &mut TraversalContext,
        addr: AccountAddress,
        ty: &Type,
    ) -> PartialVMResult<()> {
        let runtime_environment = self.loader.runtime_environment();
        let gv = if is_mut {
            self.load_resource_mut(data_cache, gas_meter, traversal_context, addr, ty)?
        } else {
            self.load_resource(data_cache, gas_meter, traversal_context, addr, ty)?
        };

        let res = gv.borrow_global();
        gas_meter.charge_borrow_global(
            is_mut,
            is_generic,
            TypeWithRuntimeEnvironment {
                ty,
                runtime_environment,
            },
            res.is_ok(),
        )?;
        self.check_resource_reentrancy(runtime_environment, ty)?;
        self.operand_stack.push(res.map_err(|err| {
            err.with_message(format!("Failed to borrow global resource from {:?}", addr))
        })?)?;
        Ok(())
    }
```

**File:** third_party/move/move-vm/runtime/src/interpreter.rs (L1554-1597)
```rust
    /// MoveTo opcode.
    fn move_to(
        &mut self,
        is_generic: bool,
        data_cache: &mut impl MoveVmDataCache,
        gas_meter: &mut impl GasMeter,
        traversal_context: &mut TraversalContext,
        addr: AccountAddress,
        ty: &Type,
        resource: Value,
    ) -> PartialVMResult<()> {
        let runtime_environment = self.loader.runtime_environment();
        let gv = self.load_resource_mut(data_cache, gas_meter, traversal_context, addr, ty)?;
        // NOTE(Gas): To maintain backward compatibility, we need to charge gas after attempting
        //            the move_to operation.
        match gv.move_to(resource) {
            Ok(()) => {
                gas_meter.charge_move_to(
                    is_generic,
                    TypeWithRuntimeEnvironment {
                        ty,
                        runtime_environment,
                    },
                    gv.view()
                        .expect("After successful move_to, global value is set"),
                    true,
                )?;
                self.check_resource_reentrancy(runtime_environment, ty)?;
                Ok(())
            },
            Err((err, resource)) => {
                gas_meter.charge_move_to(
                    is_generic,
                    TypeWithRuntimeEnvironment {
                        ty,
                        runtime_environment,
                    },
                    &resource,
                    false,
                )?;
                Err(err.with_message(format!("Failed to move resource into {:?}", addr)))
            },
        }
    }
```

**File:** third_party/move/move-vm/runtime/src/interpreter.rs (L3070-3088)
```rust
                    Instruction::MoveTo(sd_idx) => {
                        let resource = interpreter.operand_stack.pop()?;
                        let signer_reference = interpreter.operand_stack.pop_as::<SignerRef>()?;
                        let addr = signer_reference
                            .borrow_signer()?
                            .value_as::<Reference>()?
                            .read_ref()?
                            .value_as::<AccountAddress>()?;
                        let ty = self.get_struct_ty(*sd_idx);
                        interpreter.move_to(
                            false,
                            data_cache,
                            gas_meter,
                            traversal_context,
                            addr,
                            &ty,
                            resource,
                        )?;
                    },
```

**File:** third_party/move/move-vm/runtime/src/data_cache.rs (L173-199)
```rust
    fn load_resource_mut(
        &mut self,
        gas_meter: &mut impl DependencyGasMeter,
        traversal_context: &mut TraversalContext,
        addr: &AccountAddress,
        ty: &Type,
    ) -> PartialVMResult<(&mut GlobalValue, Option<NumBytes>)> {
        let bytes_loaded = if !self.data_cache.contains_resource(addr, ty) {
            let (entry, bytes_loaded) = TransactionDataCache::create_data_cache_entry(
                self.loader,
                &LayoutConverter::new(self.loader),
                gas_meter,
                traversal_context,
                self.loader.unmetered_module_storage(),
                self.resource_resolver,
                addr,
                ty,
            )?;
            self.data_cache.insert_resource(*addr, ty.clone(), entry)?;
            Some(bytes_loaded)
        } else {
            None
        };

        let gv = self.data_cache.get_resource_mut(addr, ty)?;
        Ok((gv, bytes_loaded))
    }
```

**File:** third_party/move/move-vm/runtime/src/data_cache.rs (L405-422)
```rust
    /// not exist in cache), an error is returned.
    fn get_resource_mut(
        &mut self,
        addr: &AccountAddress,
        ty: &Type,
    ) -> PartialVMResult<&mut GlobalValue> {
        if let Some(account_cache) = self.account_map.get_mut(addr) {
            if let Some(entry) = account_cache.get_mut(ty) {
                return Ok(&mut entry.value);
            }
        }

        let msg = format!("Resource for {:?} at {} must exist", ty, addr);
        let err =
            PartialVMError::new(StatusCode::UNKNOWN_INVARIANT_VIOLATION_ERROR).with_message(msg);
        Err(err)
    }
}
```
