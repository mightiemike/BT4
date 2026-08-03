[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/framework/natives/src/cryptography/algebra/new.rs (L36-38)
```rust
    let structure_opt = structure_from_ty_arg!(context, &ty_args[0]);
    abort_unless_arithmetics_enabled_for_structure!(context, structure_opt);
    match structure_opt {
```

**File:** aptos-move/framework/natives/src/cryptography/algebra/arithmetics/inv.rs (L41-66)
```rust
    let structure_opt = structure_from_ty_arg!(context, &ty_args[0]);
    abort_unless_arithmetics_enabled_for_structure!(context, structure_opt);
    match structure_opt {
        Some(Structure::BLS12381Fr) => ark_inverse_internal!(
            context,
            args,
            ark_bls12_381::Fr,
            ALGEBRA_ARK_BLS12_381_FR_INV
        ),
        Some(Structure::BLS12381Fq12) => ark_inverse_internal!(
            context,
            args,
            ark_bls12_381::Fq12,
            ALGEBRA_ARK_BLS12_381_FQ12_INV
        ),
        Some(Structure::BN254Fr) => {
            ark_inverse_internal!(context, args, ark_bn254::Fr, ALGEBRA_ARK_BN254_FR_INV)
        },
        Some(Structure::BN254Fq) => {
            ark_inverse_internal!(context, args, ark_bn254::Fq, ALGEBRA_ARK_BN254_FQ_INV)
        },
        Some(Structure::BN254Fq12) => {
            ark_inverse_internal!(context, args, ark_bn254::Fq12, ALGEBRA_ARK_BN254_FQ12_INV)
        },
        _ => Err(SafeNativeError::abort(MOVE_ABORT_CODE_NOT_IMPLEMENTED)),
    }
```

**File:** aptos-move/framework/natives/src/cryptography/algebra/arithmetics/double.rs (L26-72)
```rust
    let structure_opt = structure_from_ty_arg!(context, &ty_args[0]);
    abort_unless_arithmetics_enabled_for_structure!(context, structure_opt);
    match structure_opt {
        Some(Structure::BLS12381G1) => ark_unary_op_internal!(
            context,
            args,
            ark_bls12_381::G1Projective,
            double,
            ALGEBRA_ARK_BLS12_381_G1_PROJ_DOUBLE
        ),
        Some(Structure::BLS12381G2) => ark_unary_op_internal!(
            context,
            args,
            ark_bls12_381::G2Projective,
            double,
            ALGEBRA_ARK_BLS12_381_G2_PROJ_DOUBLE
        ),
        Some(Structure::BLS12381Gt) => ark_unary_op_internal!(
            context,
            args,
            ark_bls12_381::Fq12,
            square,
            ALGEBRA_ARK_BLS12_381_FQ12_SQUARE
        ),
        Some(Structure::BN254G1) => ark_unary_op_internal!(
            context,
            args,
            ark_bn254::G1Projective,
            double,
            ALGEBRA_ARK_BN254_G1_PROJ_DOUBLE
        ),
        Some(Structure::BN254G2) => ark_unary_op_internal!(
            context,
            args,
            ark_bn254::G2Projective,
            double,
            ALGEBRA_ARK_BN254_G2_PROJ_DOUBLE
        ),
        Some(Structure::BN254Gt) => ark_unary_op_internal!(
            context,
            args,
            ark_bn254::Fq12,
            square,
            ALGEBRA_ARK_BN254_FQ12_SQUARE
        ),
        _ => Err(SafeNativeError::abort(MOVE_ABORT_CODE_NOT_IMPLEMENTED)),
    }
```

**File:** aptos-move/framework/natives/src/cryptography/algebra/mod.rs (L52-53)
```rust
/// Equivalent to `std::error::not_implemented(0)` in Move.
const MOVE_ABORT_CODE_NOT_IMPLEMENTED: u64 = 0x0C_0001;
```
