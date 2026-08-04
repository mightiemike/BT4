[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** third_party/move/move-vm/runtime/src/native_functions.rs (L420-456)
```rust
    fn verify_function(
        &mut self,
        module: Arc<Module>,
        func: Arc<Function>,
        expected_ty: &Type,
    ) -> PartialVMResult<Result<Box<dyn AbstractFunction>, FunctionResolutionError>> {
        use FunctionResolutionError::*;
        if !func.is_public() {
            return Ok(Err(FunctionNotAccessible));
        }
        let Type::Function {
            args,
            results,
            abilities,
        } = expected_ty
        else {
            return Ok(Err(FunctionIncompatibleType));
        };

        // A resolved function is always public (checked above) and captures no arguments, so the
        // resulting closure value has exactly the abilities of a public function value, i.e.,
        // `PUBLIC_FUNCTIONS` (copy + drop + store). Reject any expected type whose abilities are
        // not a subset of these (e.g., one requesting `key`), since no such closure value can
        // exist and returning it would let the caller treat the closure as a type it is not.
        if !abilities.is_subset(AbilitySet::PUBLIC_FUNCTIONS) {
            return Ok(Err(FunctionIncompatibleType));
        }

        let func_ref = func.as_ref();

        // Match types, inferring instantiation of function in `subst`.
        let mut subst = TypeParamMap::default();
        if !subst.match_tys(func_ref.param_tys.iter(), args.iter())
            || !subst.match_tys(func_ref.return_tys.iter(), results.iter())
        {
            return Ok(Err(FunctionIncompatibleType));
        }
```

**File:** aptos-move/framework/move-stdlib/sources/reflect.move (L36-44)
```text
    public fun resolve<FuncType>(
        addr: address, module_name: &String, func_name: &String
    ): Result<FuncType, ReflectionError> {
        assert!(
            features::is_function_reflection_enabled(),
            error::invalid_state(E_FEATURE_NOT_ENABLED)
        );
        native_resolve(addr, module_name, func_name)
    }
```
