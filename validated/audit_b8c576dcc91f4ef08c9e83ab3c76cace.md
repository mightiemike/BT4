No vulnerability found for this question.

**Reasoning:** The premise doesn't hold up. `verify_init_module_function` in [1](#0-0)  deliberately accepts both `signer` and `&signer` via `is_signer_or_signer_ref()` — this is documented, intended behavior ("single signer (reference) input"), matching the same rule enforced at compile time in [2](#0-1) .

Whether `init_module` takes `signer` by value or `&signer` by reference has no bearing on write-set determinism:

1. Move VM execution is fully deterministic for a given bytecode and inputs — the `MoveTo`/`MoveToGeneric` bytecode instructions always require a `SignerRef` on the operand stack ( [3](#0-2) ). If a package's `init_module` takes `signer` by value, the Move compiler must emit a `borrow_loc`/`ImmBorrowLoc` on that local before calling `move_to` — the bytecode itself encodes this, it's not something the VM decides at runtime.
2. There is no "reference implementation" divergence to speak of: every honest validator runs the identical deterministic interpreter ( [4](#0-3) ) against the identical bytecode, producing byte-identical results every time. The transactional test `signer_usage.move` confirms passing `signer` by value and later borrowing/moving it works exactly as expected within normal Move semantics ( [5](#0-4) ).
3. `execute_loaded_function`/`execute_loaded_function_with_tracing` ( [6](#0-5) ) deserializes the `signer` argument according to `param_tys` and dispatches to the same interpreter regardless of by-value/by-reference — there's no code path where accepting one over the other causes a different account/resource-group write-set for semantically equivalent logic.

Since Move VM execution is deterministic and this is purely a language-level typing distinction with no effect on the resulting write set, there's no state-integrity issue, no hard-fork-only divergence, and no way for unprivileged input to corrupt committed state via this mechanism.

### Citations

**File:** aptos-move/aptos-vm/src/verifier/module_init.rs (L88-95)
```rust
    let arg_ty = &param_tys[0];
    if !arg_ty.is_signer_or_signer_ref() {
        return err(
            "init_module function expects a single signer or &signer parameter, \
             but its parameter type is different"
                .to_string(),
        );
    }
```

**File:** aptos-move/framework/src/extended_checks.rs (L158-172)
```rust
            if fun.get_parameter_count() != 1 {
                record_param_mismatch_error();
            } else {
                let Parameter(_, ty, _) = &fun.get_parameters()[0];
                let ok = match ty {
                    Type::Primitive(PrimitiveType::Signer) => true,
                    Type::Reference(_, ty) => {
                        matches!(ty.as_ref(), Type::Primitive(PrimitiveType::Signer))
                    },
                    _ => false,
                };
                if !ok {
                    record_param_mismatch_error();
                }
            }
```

**File:** third_party/move/move-vm/runtime/src/interpreter.rs (L334-353)
```rust
    fn execute_main<RTTCheck: RuntimeTypeCheck, RTRCheck: RuntimeRefCheck>(
        mut self,
        data_cache: &mut impl MoveVmDataCache,
        function_caches: &mut InterpreterFunctionCaches,
        gas_meter: &mut impl GasMeter,
        traversal_context: &mut TraversalContext,
        extensions: &mut NativeContextExtensions,
        trace_recorder: &mut impl TraceRecorder,
        function: Rc<LoadedFunction>,
        args: Vec<Value>,
    ) -> VMResult<Vec<Value>> {
        let fn_guard = VM_PROFILER.function_start(function.as_ref());

        let num_locals = function.local_tys().len();
        let mut locals = Locals::new(num_locals);
        for (i, value) in args.into_iter().enumerate() {
            locals
                .store_loc(i, value)
                .map_err(|e| self.set_location(e))?;
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

**File:** third_party/move/move-vm/transactional-tests/tests/native_functions/signer_usage.move (L9-15)
```text
    public entry fun borrow_then_move(account: signer) {
        let addr_ref = signer::borrow_address(&account);
        let addr = *addr_ref;

        move_to(&account, Marker { dummy: true });
        let _ = move_from<Marker>(addr);
    }
```

**File:** third_party/move/move-vm/runtime/src/move_vm.rs (L80-136)
```rust
    pub fn execute_loaded_function_with_tracing(
        function: LoadedFunction,
        serialized_args: Vec<impl Borrow<[u8]>>,
        data_cache: &mut impl MoveVmDataCache,
        gas_meter: &mut impl GasMeter,
        traversal_context: &mut TraversalContext,
        extensions: &mut NativeContextExtensions,
        loader: &impl Loader,
        trace_recorder: &mut impl TraceRecorder,
    ) -> VMResult<SerializedReturnValues> {
        let vm_config = loader.runtime_environment().vm_config();

        let function_value_extension = FunctionValueExtensionAdapter {
            module_storage: loader.unmetered_module_storage(),
        };
        let layout_converter = LayoutConverter::new(loader);
        let ty_depth_checker = TypeDepthChecker::new(loader);

        let create_ty_with_subst = |tys: &[Type]| -> VMResult<Vec<Type>> {
            let ty_builder = &vm_config.ty_builder;
            tys.iter()
                .map(|ty| ty_builder.create_ty_with_subst(ty, function.ty_args()))
                .collect::<PartialVMResult<Vec<_>>>()
                .map_err(|err| err.finish(Location::Undefined))
        };

        let param_tys = create_ty_with_subst(function.param_tys())?;
        let (mut dummy_locals, deserialized_args) = deserialize_args(
            &function_value_extension,
            &layout_converter,
            gas_meter,
            traversal_context,
            &param_tys,
            serialized_args,
        )
        .map_err(|err| err.finish(Location::Undefined))?;

        let return_tys = create_ty_with_subst(function.return_tys())?;

        let return_values = {
            let _timer = VM_TIMER.timer_with_label("Interpreter::entrypoint");

            Interpreter::entrypoint(
                function,
                deserialized_args,
                data_cache,
                // TODO(caches): async drop
                &mut InterpreterFunctionCaches::new(),
                loader,
                &ty_depth_checker,
                &layout_converter,
                gas_meter,
                traversal_context,
                extensions,
                trace_recorder,
            )?
        };
```
