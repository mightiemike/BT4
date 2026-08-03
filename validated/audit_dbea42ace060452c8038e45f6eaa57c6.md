[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** aptos-move/framework/natives/src/cryptography/algebra/mod.rs (L114-120)
```rust
#[macro_export]
macro_rules! structure_from_ty_arg {
    ($context:expr, $typ:expr) => {{
        let type_tag = $context.type_to_type_tag($typ)?;
        Structure::try_from(type_tag).ok()
    }};
}
```

**File:** aptos-move/framework/natives/src/cryptography/algebra/mod.rs (L188-202)
```rust
impl TryFrom<TypeTag> for HashToStructureSuite {
    type Error = ();

    fn try_from(value: TypeTag) -> Result<Self, Self::Error> {
        match value.to_canonical_string().as_str() {
            "0x1::bls12381_algebra::HashG1XmdSha256SswuRo" => {
                Ok(HashToStructureSuite::Bls12381g1XmdSha256SswuRo)
            },
            "0x1::bls12381_algebra::HashG2XmdSha256SswuRo" => {
                Ok(HashToStructureSuite::Bls12381g2XmdSha256SswuRo)
            },
            _ => Err(()),
        }
    }
}
```

**File:** aptos-move/framework/natives/src/cryptography/algebra/mod.rs (L244-261)
```rust
/// Try getting a pointer to the `handle`-th elements in `context` and assign it to a local variable `ptr_out`.
/// Then try casting it to a reference of `typ` and assign it in a local variable `ref_out`.
/// Abort the VM execution with invariant violation if anything above fails.
#[macro_export]
macro_rules! safe_borrow_element {
    ($context:expr, $handle:expr, $typ:ty, $ptr_out:ident, $ref_out:ident) => {
        let $ptr_out = $context
            .extensions()
            .get::<AlgebraContext>()
            .objs
            .get($handle)
            .ok_or_else(abort_invariant_violated)?
            .clone();
        let $ref_out = $ptr_out
            .downcast_ref::<$typ>()
            .ok_or_else(abort_invariant_violated)?;
    };
}
```

**File:** aptos-move/framework/natives/src/cryptography/algebra/mod.rs (L263-284)
```rust
#[macro_export]
macro_rules! store_element {
    ($context:expr, $obj:expr) => {{
        let context = &mut $context.extensions_mut().get_mut::<AlgebraContext>();
        let new_size = context.bytes_used + std::mem::size_of_val(&$obj);
        if new_size > MEMORY_LIMIT_IN_BYTES {
            Err(SafeNativeError::abort_with_message(
                E_TOO_MUCH_MEMORY_USED,
                format!(
                    "Algebra context memory {}-byte limit exceeded: currently using {} bytes; was asked for {} bytes",
                    MEMORY_LIMIT_IN_BYTES, context.bytes_used, new_size,
                ),
            ))
        } else {
            let target_vec = &mut context.objs;
            context.bytes_used = new_size;
            let ret = target_vec.len();
            target_vec.push(Rc::new($obj));
            Ok(ret)
        }
    }};
}
```

**File:** aptos-move/framework/natives/src/cryptography/algebra/casting.rs (L54-82)
```rust
    match (super_opt, sub_opt) {
        (Some(Structure::BLS12381Fq12), Some(Structure::BLS12381Gt)) => {
            let handle = safely_pop_arg!(args, u64) as usize;
            safe_borrow_element!(context, handle, ark_bls12_381::Fq12, element_ptr, element);
            let r_scalar = BLS12381_R_SCALAR.as_ref().ok_or_else(|| {
                SafeNativeError::abort_with_message(
                    E_CASTING_BLS12381_R_SCALAR_LOADING_FAILED,
                    "BLS12381 R scalar loading failed",
                )
            })?;
            context.charge(ALGEBRA_ARK_BLS12_381_FQ12_POW_U256)?;
            if element.pow(r_scalar.0) == ark_bls12_381::Fq12::one() {
                Ok(smallvec![Value::bool(true), Value::u64(handle as u64)])
            } else {
                Ok(smallvec![Value::bool(false), Value::u64(handle as u64)])
            }
        },
        (Some(Structure::BN254Fq12), Some(Structure::BN254Gt)) => {
            let handle = safely_pop_arg!(args, u64) as usize;
            safe_borrow_element!(context, handle, ark_bn254::Fq12, element_ptr, element);
            context.charge(ALGEBRA_ARK_BN254_FQ12_POW_U256)?;
            if element.pow(BN254_R_SCALAR.0) == ark_bn254::Fq12::one() {
                Ok(smallvec![Value::bool(true), Value::u64(handle as u64)])
            } else {
                Ok(smallvec![Value::bool(false), Value::u64(handle as u64)])
            }
        },
        _ => Err(SafeNativeError::abort(MOVE_ABORT_CODE_NOT_IMPLEMENTED)),
    }
```
