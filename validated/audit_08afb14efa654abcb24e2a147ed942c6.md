[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** third_party/move/move-vm/runtime/src/loader/modules.rs (L291-299)
```rust
        for func_inst in module.function_instantiations() {
            let handle = function_refs[func_inst.handle.0 as usize].clone();
            let idx = func_inst.type_parameters.0 as usize;
            let instantiation = signature_table[idx].clone();
            let ty_args_id = if is_fully_instantiated_signature[idx] {
                Some(ty_pool.intern_ty_args(&instantiation))
            } else {
                None
            };
```

**File:** third_party/move/move-vm/runtime/src/loader/script.rs (L88-106)
```rust
        let mut function_instantiations = vec![];
        for func_inst in script.function_instantiations() {
            let handle = function_refs[func_inst.handle.0 as usize].clone();
            let (instantiation, is_fully_instantiated) = convert_toks_to_types(
                BinaryIndexedView::Script(&script),
                &script.signature_at(func_inst.type_parameters).0,
                &struct_names,
            )?;
            let ty_args_id = if is_fully_instantiated {
                Some(ty_pool.intern_ty_args(&instantiation))
            } else {
                None
            };
            function_instantiations.push(FunctionInstantiation {
                handle,
                instantiation,
                ty_args_id,
            });
        }
```

**File:** third_party/move/move-vm/runtime/src/frame.rs (L581-589)
```rust
        let instantiation = instantiation
            .iter()
            .map(|ty| self.ty_builder.create_ty_with_subst(ty, ty_args))
            .collect::<PartialVMResult<Vec<_>>>()?;
        let ty_args_id = match ty_args_id {
            Some(ty_args_id) => ty_args_id,
            // We can hit this case where original type args were only a partial instantiation.
            None => ty_pool.intern_ty_args(&instantiation),
        };
```

**File:** third_party/move/move-vm/types/src/ty_interner.rs (L238-247)
```rust
    /// Given a vector if fully-instantiated type arguments, returns the corresponding [TypeVecId].
    ///
    /// Panics if there are non-instantiated type arguments.
    pub fn intern_ty_args(&self, ty_args: &[Type]) -> TypeVecId {
        let ty_args = ty_args
            .iter()
            .map(|t| self.instantiate_and_intern(t, &[]))
            .collect::<Vec<_>>();
        self.ty_vec_interner.intern_vec(ty_args)
    }
```
