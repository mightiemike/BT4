[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/natives/src/storage_slot.rs (L90-131)
```rust
fn native_borrow_storage_slot_resource_mut(
    context: &mut SafeNativeContext,
    ty_args: &[Type],
    mut args: VecDeque<Value>,
) -> SafeNativeResult<SmallVec<[Value; 1]>> {
    safely_assert_eq!(ty_args.len(), 2);
    safely_assert_eq!(args.len(), 1);

    context.charge(STORAGE_SLOT_BORROW_MUT_BASE)?;

    // Get the address from StorageSlot.addr field
    let storage_slot_ref = safely_pop_arg!(args, StructRef);
    let addr = storage_slot_ref
        .borrow_field(0)?
        .value_as::<Reference>()?
        .read_ref()?
        .value_as::<AccountAddress>()?;

    // ty_args[1] is StorageSlotResource<T> - the type we want to borrow from global storage
    let storage_slot_resource_ty = &ty_args[1];

    // Borrow the resource mutably from global storage
    let (ref_val, num_bytes) = context
        .borrow_resource_mut(addr, storage_slot_resource_ty)
        .map_err(|err| {
            // Check if resource doesn't exist
            if err.major_status() == StatusCode::MISSING_DATA {
                SafeNativeError::abort_with_message(
                    ESTORAGE_SLOT_NOT_FOUND,
                    format!("StorageSlotResource at address {} not found", addr),
                )
            } else {
                err.into()
            }
        })?;

    // Charge for loaded bytes
    if let Some(num_bytes) = num_bytes {
        context.charge(STORAGE_SLOT_BORROW_MUT_PER_BYTE_LOADED * num_bytes)?;
    }

    Ok(smallvec![ref_val])
```

**File:** third_party/move/move-vm/runtime/src/native_functions.rs (L166-190)
```rust
    /// Borrows an immutable reference to a resource in global storage.
    /// Returns the reference value and the number of bytes loaded.
    pub fn borrow_resource(
        &mut self,
        address: AccountAddress,
        ty: &Type,
    ) -> PartialVMResult<(Value, Option<NumBytes>)> {
        self.data_cache
            .native_borrow_resource(self.gas_meter, self.traversal_context, &address, ty)
    }

    /// Borrows a mutable reference to a resource in global storage.
    /// Returns the reference value and the number of bytes loaded.
    pub fn borrow_resource_mut(
        &mut self,
        address: AccountAddress,
        ty: &Type,
    ) -> PartialVMResult<(Value, Option<NumBytes>)> {
        self.data_cache.native_borrow_resource_mut(
            self.gas_meter,
            self.traversal_context,
            &address,
            ty,
        )
    }
```

**File:** aptos-move/framework/aptos-framework/sources/datastructures/storage_slot.move (L25-36)
```text
    native fun borrow_storage_slot_resource<T: store, BR>(self: &StorageSlot<T>): &BR;
    native fun borrow_storage_slot_resource_mut<T: store, BR>(self: &mut StorageSlot<T>): &mut BR;

    public fun borrow<T: store>(self: &StorageSlot<T>): &T {
        assert!(std::features::is_storage_slot_natives_enabled(), ESTORAGE_SLOT_NATIVES_NOT_ENABLED);
        &self.borrow_storage_slot_resource<T, StorageSlotResource<T>>().val
    }

    public fun borrow_mut<T: store>(self: &mut StorageSlot<T>): &mut T {
        assert!(std::features::is_storage_slot_natives_enabled(), ESTORAGE_SLOT_NATIVES_NOT_ENABLED);
        &mut self.borrow_storage_slot_resource_mut<T, StorageSlotResource<T>>().val
    }
```
