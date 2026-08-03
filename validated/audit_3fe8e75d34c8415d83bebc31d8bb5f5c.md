[1](#0-0) [2](#0-1)

### Citations

**File:** third_party/move/move-vm/runtime/src/native_functions.rs (L459-470)
```rust
        let ty_args = match subst.verify_and_extract_type_args(func_ref.ty_param_abilities()) {
            Ok(ty_args) => ty_args,
            Err(err) => match err.major_status() {
                StatusCode::NUMBER_OF_TYPE_ARGUMENTS_MISMATCH => {
                    return Ok(Err(FunctionNotInstantiated));
                },
                StatusCode::CONSTRAINT_NOT_SATISFIED => {
                    return Ok(Err(FunctionIncompatibleType));
                },
                _ => return Err(err),
            },
        };
```
