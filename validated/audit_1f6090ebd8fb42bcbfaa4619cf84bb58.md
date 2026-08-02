[1](#0-0) [2](#0-1)

### Citations

**File:** crates/aptos-crypto/src/blstrs/polynomials.rs (L276-293)
```rust
pub fn poly_mul_assign_fft_with_dom(
    f: &mut Vec<Scalar>,
    g: &mut Vec<Scalar>,
    dom: &EvaluationDomain,
) {
    debug_assert!(!f.is_empty());
    debug_assert!(!g.is_empty());
    debug_assert_eq!((f.len() - 1) + (g.len() - 1) + 1, dom.n);

    fft::fft_assign(f, dom);
    fft::fft_assign(g, dom);
    for i in 0..dom.N {
        f[i].mul_assign(g[i]);
    }

    fft::ifft_assign(f, dom);
    f.truncate(dom.n);
}
```

**File:** crates/aptos-crypto/src/blstrs/evaluation_domain.rs (L73-95)
```rust
    pub fn new(n: usize) -> Result<EvaluationDomain, CryptoMaterialError> {
        // Compute the size of our evaluation domain
        let (N, log_N) = smallest_power_of_2_greater_than_or_eq(n);

        // The pairing-friendly curve may not be able to support
        // large enough (radix2) evaluation domains.
        if log_N >= Scalar::S as usize {
            return Err(CryptoMaterialError::WrongLengthError);
        }

        // Compute $\omega$, the $N$th primitive root of unity
        let omega = Self::get_Nth_root_of_unity(log_N);

        Ok(EvaluationDomain {
            n,
            N,
            log_N,
            omega,
            omega_inverse: omega.invert().unwrap(),
            // geninv: Scalar::multiplicative_generator().invert().unwrap(),
            N_inverse: Scalar::from(N as u64).invert().unwrap(),
        })
    }
```
