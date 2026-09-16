## Title
Missing modulus range check on Secp256k1/Secp256r1 point coordinates in Cairo Native `secp256k1_add`/`secp256k1_mul`/`secp256r1_add`/`secp256r1_mul` syscalls - (File: crates/blockifier/src/execution/native/syscall_handler.rs)

### Summary
In the Cairo Native execution path, `Secp256k1Point`/`Secp256r1Point` values passed into the `secp256k1_add`, `secp256k1_mul`, `secp256r1_add`, and `secp256r1_mul` syscall handlers are converted directly from raw `U256` coordinates via `Secp256Point::from` / `u256_to_big4int` with **no modulus bound check**, unlike `Secp256Point::new` and `Secp256Point::get_point_from_x`, which explicitly call `secp::new_affine`/`secp::get_point_from_x` that enforce `modulus_bound_check`.

### Finding Description
`crates/blockifier/src/execution/native/syscall_handler.rs` defines conversions: [1](#0-0) 
These `From<Secp256k1Point>`/`From<Secp256r1Point>` impls build an `Affine<Curve>` point directly from user-supplied `x`/`y` coordinates with no validation that `x`/`y` are less than the curve's base-field modulus, contrary to the documented invariant in `secp::new_affine`/`secp::get_point_from_x`: [2](#0-1) 

The `add`/`mul` handlers use these unchecked conversions directly: [3](#0-2) [4](#0-3) 

By contrast, `secp::get_point_from_x` and `secp::new_affine` explicitly perform a `modulus_bound_check` before constructing any `Affine` point: [5](#0-4) 

This mirrors the reported bug class: values that should be bound-checked against a field modulus before being used in downstream arithmetic are consumed without that check, because the check is applied only on some entry points (`new`/`get_point_from_x`) and not others (`add`/`mul`) that accept the same raw coordinate representation.

**Important caveat on reachability**: I could not fully confirm within the available tool budget whether the Cairo Native ABI actually allows a contract to supply arbitrary raw `Secp256k1Point`/`Secp256r1Point` structs directly to `secp256k1_add`/`secp256k1_mul` (i.e., whether these are plain value types passed across the Cairo Native/Rust FFI boundary without any prior "was this point validated" tracking), or whether the `cairo_native` runtime/libfunc layer enforces that such point values can only originate from a prior `secp256k1_new`/`secp256k1_get_point_from_x` call. In the CASM/Cairo VM path (`crates/blockifier/src/execution/syscalls/secp.rs`), points are stored as opaque handles (`Relocatable` pointers) in a `HashMap` populated only by validated `secp_new`/`secp_get_point_from_x` calls, so `secp_add`/`secp_mul` there are safe. The native path's type signatures (`fn secp256k1_add(&mut self, p0: Secp256k1Point, p1: Secp256k1Point, ...)`) suggest points are passed by value (raw coordinates), which would make this reachable, but I was unable to inspect the `cairo_native` crate's syscall trait/ABI definitions to confirm this with certainty.

### Impact Explanation
If reachable, a contract could invoke `secp256k1_add`/`secp256k1_mul` (or the r1 variants) with coordinates `x`/`y` at or above the base-field modulus. This would cause the `arkworks` `Affine`/`Projective` arithmetic to operate on non-canonical field representations, producing results that diverge from the CASM/Cairo-VM re-execution and Starknet OS execution of the same syscall (which enforces the modulus check via `secp::new_affine`/`get_point_from_x` before any point can be added/multiplied). This is an honest-node divergence risk: nodes executing via Cairo Native vs. the Cairo VM (or the Starknet OS prover) could compute different results for the same transaction, breaking state-commitment consistency, since the same contract call could produce different results depending on whether it is executed by Cairo Native or the CASM/VM path.

### Likelihood Explanation
Likelihood cannot be confirmed as reasonably high without verifying that a malicious contract can pass raw, unvalidated coordinate values through the Cairo Native syscall ABI directly to `secp256k1_add`/`mul` bypassing `secp256k1_new`. If the `cairo_native` libfunc lowering only permits secp points to be produced by `secp256k1_new`/`get_point_from_x` (which do check the modulus) and disallows constructing a `Secp256k1Point` from arbitrary felts, then this finding would not be exploitable and the missing check would only be a defense-in-depth gap, not an exploitable bug.

### Recommendation
Add the same `modulus_bound_check` used in `secp::new_affine`/`secp::get_point_from_x` to the `From<Secp256k1Point>`/`From<Secp256r1Point>` conversions (or explicitly at the start of `secp256k1_add`/`secp256k1_mul`/`secp256r1_add`/`secp256r1_mul` in `crates/blockifier/src/execution/native/syscall_handler.rs`), so that every entry point that constructs an `Affine<Curve>` from raw `U256` coordinates validates `x < modulus` and `y < modulus` before performing curve arithmetic, matching the CASM/Cairo-VM execution path's guarantees.

### Proof of Concept
Not fully verifiable without access to the `cairo_native` syscall trait/ABI to confirm whether `secp256k1_add`/`secp256k1_mul` can be invoked with attacker-chosen raw coordinates bypassing `secp256k1_new`. Conceptually: a contract compiled with Cairo Native would call `secp256k1_add(p0, p1)` where `p0`/`p1` are `Secp256k1Point{x, y}` with `x` or `y` ≥ the secp256k1 base field modulus, producing an affine point whose arithmetic result differs from what the CASM/Cairo VM execution (which enforces the modulus check) would produce for the same call, given points are validated to be `< modulus` before entering the point registry in `crates/blockifier/src/execution/syscalls/secp.rs`.

### Citations

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L681-709)
```rust
    fn secp256k1_add(
        &mut self,
        p0: Secp256k1Point,
        p1: Secp256k1Point,
        remaining_gas: &mut u64,
    ) -> SyscallResult<Secp256k1Point> {
        self.pre_execute_syscall(
            remaining_gas,
            self.gas_costs().syscalls.secp256k1_add.base_syscall_cost(),
            SyscallSelector::Secp256k1Add,
        )?;

        Ok(Secp256Point::add(p0.into(), p1.into()).into())
    }

    fn secp256k1_mul(
        &mut self,
        p: Secp256k1Point,
        m: U256,
        remaining_gas: &mut u64,
    ) -> SyscallResult<Secp256k1Point> {
        self.pre_execute_syscall(
            remaining_gas,
            self.gas_costs().syscalls.secp256k1_mul.base_syscall_cost(),
            SyscallSelector::Secp256k1Mul,
        )?;

        Ok(Secp256Point::mul(p.into(), m).into())
    }
```

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L896-904)
```rust
impl From<Secp256k1Point> for Secp256Point<ark_secp256k1::Config> {
    fn from(p: Secp256k1Point) -> Self {
        Secp256Point(Affine {
            x: u256_to_big4int(p.x).into(),
            y: u256_to_big4int(p.y).into(),
            infinity: p.is_infinity,
        })
    }
}
```

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L933-943)
```rust
    /// Given an (x, y) pair, this function:
    /// - Returns the point at infinity for (0, 0).
    /// - Returns `Err` if either `x` or `y` is outside the modulus.
    /// - Returns `Ok(None)` if (x, y) are within the modulus but not on the curve.
    /// - Ok(Some(Point)) if (x,y) are on the curve.
    fn new(x: U256, y: U256) -> Result<Option<Self>, SyscallExecutionError> {
        let x = u256_to_biguint(x);
        let y = u256_to_biguint(y);

        Self::wrap_secp_result(Ok(secp::new_affine(x, y)?))
    }
```

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L945-953)
```rust
    fn add(p0: Self, p1: Self) -> Self {
        let result: Projective<Curve> = p0.0 + p1.0;
        Secp256Point(result.into())
    }

    fn mul(p: Self, m: U256) -> Self {
        let result = p.0 * Curve::ScalarField::from(u256_to_biguint(m));
        Secp256Point(result.into())
    }
```

**File:** crates/blockifier/src/execution/secp.rs (L7-53)
```rust
pub fn get_point_from_x<Curve: SWCurveConfig>(
    x: num_bigint::BigUint,
    y_parity: bool,
) -> Result<Option<Affine<Curve>>, SyscallExecutorBaseError>
where
    Curve::BaseField: PrimeField, // constraint for get_point_by_id
{
    modulus_bound_check::<Curve>(&[&x])?;

    let x = x.into();
    let maybe_ec_point = Affine::<Curve>::get_ys_from_x_unchecked(x)
        .map(|(smaller, greater)| {
            // Return the correct y coordinate based on the parity.
            if smaller.into_bigint().is_odd() == y_parity { smaller } else { greater }
        })
        .map(|y| Affine::<Curve>::new_unchecked(x, y))
        .filter(|p| p.is_in_correct_subgroup_assuming_on_curve());

    Ok(maybe_ec_point)
}

pub fn new_affine<Curve: SWCurveConfig>(
    x: num_bigint::BigUint,
    y: num_bigint::BigUint,
) -> Result<Option<Affine<Curve>>, SyscallExecutorBaseError>
where
    Curve::BaseField: PrimeField, // constraint for get_point_by_id
{
    modulus_bound_check::<Curve>(&[&x, &y])?;

    Ok(maybe_affine(x.into(), y.into()))
}

fn modulus_bound_check<Curve: SWCurveConfig>(
    bounds: &[&num_bigint::BigUint],
) -> Result<(), SyscallExecutorBaseError>
where
    Curve::BaseField: PrimeField, // constraint for get_point_by_id
{
    let modulus = Curve::BaseField::MODULUS.into();

    if bounds.iter().any(|p| **p >= modulus) {
        return Err(SyscallExecutorBaseError::Revert { error_data: vec![INVALID_ARGUMENT_FELT] });
    }

    Ok(())
}
```
