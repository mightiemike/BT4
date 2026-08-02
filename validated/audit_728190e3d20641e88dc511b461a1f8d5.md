No vulnerability found for this question.

**Reasoning:**

The claim that `fft_assign` can silently corrupt the accumulator polynomial when `poly.len() > dom.N` is incorrect.

1. `fft_assign` only pads (`poly.resize(dom.N, ...)`) when `poly.len() < dom.N`; it performs no truncation when `poly.len() > dom.N` [1](#0-0) . However, the resulting oversized slice is then passed to `serial_fft_assign`, which contains a hard `assert_eq!(n, 1 << log_n)` check (not a `debug_assert!`) that compares the actual slice length against the expected power-of-two size derived from `dom.log_N` [2](#0-1) . Since `dom.N == 1 << dom.log_N` by construction in `EvaluationDomain::new` [3](#0-2) , any mismatch between `poly.len()` and `dom.N` causes this assertion to fail deterministically — this is a real `assert_eq!`, which is compiled into both debug and release builds, so it cannot be silently bypassed by an unprivileged input.

2. Tracing the actual call site referenced in the exploit (`lagrange_coefficients`), the accumulator polynomial `Z(X)` is deliberately shaped before the `fft_assign` call so its length always exactly matches `dom.N`. `accumulator_poly_helper` explicitly special-cases the "N-out-of-N" scenario (where `T.len() == N`) by splitting the polynomial construction to produce a degree-`N` polynomial via `poly_mul_slow` instead of an FFT-based multiplication that would otherwise require an unavailable evaluation domain of size `2N` [4](#0-3) . The subsequent `poly_differentiate(&mut Z)` call reduces the polynomial's length by exactly one coefficient, restoring it to exactly `dom.N` before `fft_assign` is invoked [5](#0-4)  and [6](#0-5) .

3. Even in a hypothetical path where an oversized `T` (with `T.len() > N`) slipped past the `debug_assert_le!(t, N)` check in `lagrange_coefficients` (which is compiled out in release builds) [7](#0-6) , the recursive `accumulator_poly` calls would hit `BatchEvaluationDomain::get_subdomain`'s `assert_le!(k, self.omegas.len())` check first, which panics deterministically before any oversized vector could reach `fft_assign` [8](#0-7) .

In all traced paths, a length mismatch results in a deterministic panic (`assert_eq!` / `assert_le!`), not silent corruption of Lagrange denominators or the reconstructed secret. A deterministic panic is a crash/DoS condition, which is explicitly out of scope per the review rules ("Ignore ... generic DoS"), and does not satisfy the State-Integrity Gate, which requires silent corruption of committed state, proofs, or authenticated responses.

### Citations

**File:** crates/aptos-crypto/src/blstrs/fft.rs (L14-21)
```rust
pub fn fft_assign(poly: &mut Vec<Scalar>, dom: &EvaluationDomain) {
    // Pad with zeros, if necessary
    if poly.len() < dom.N {
        poly.resize(dom.N, Scalar::ZERO);
    }

    serial_fft_assign(poly.as_mut_slice(), &dom.omega, dom.log_N as u32)
}
```

**File:** crates/aptos-crypto/src/blstrs/fft.rs (L62-73)
```rust
fn serial_fft_assign(a: &mut [Scalar], omega: &Scalar, log_n: u32) {
    fn bitreverse(mut n: u32, l: u32) -> u32 {
        let mut r = 0;
        for _ in 0..l {
            r = (r << 1) | (n & 1);
            n >>= 1;
        }
        r
    }

    let n = a.len() as u32;
    assert_eq!(n, 1 << log_n);
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

**File:** crates/aptos-crypto/src/blstrs/evaluation_domain.rs (L163-169)
```rust
    pub fn get_subdomain(&self, k: usize) -> EvaluationDomain {
        assert_le!(k, self.omegas.len());
        assert_ne!(k, 0);

        let (K, log_K) = smallest_power_of_2_greater_than_or_eq(k);
        assert_gt!(K, 0);

```

**File:** crates/aptos-crypto/src/blstrs/lagrange.rs (L141-148)
```rust
    let N = dom.N();
    let t = T.len();
    assert_gt!(N, 0);

    // Technically, the accumulator poly has degree t, so we need to evaluate it on t+1 points, which
    // will be a problem when t = N, because the evaluation domain will be of size N, not N+1. However,
    // we handle this in `accumulator_poly_helper`
    debug_assert_le!(t, N);
```

**File:** crates/aptos-crypto/src/blstrs/lagrange.rs (L166-174)
```rust
    // Compute Z'(X), in place, overwriting Z(X)
    poly_differentiate(&mut Z);

    // Compute $Z'(\omega^i)$ for all $i\in [0, N)$, in place, overwriting $Z'(X)$.
    // (We only need $t$ of them, but computing all of them via an FFT is faster than computing them
    // via a multipoint evaluation.)
    //
    // NOTE: The FFT implementation could be parallelized, but only 17.7% of the time is spent here.
    fft_assign(&mut Z, &dom.get_subdomain(N));
```

**File:** crates/aptos-crypto/src/blstrs/lagrange.rs (L204-238)
```rust
    // TODO(Performance): This is the performance bottleneck: 75.58% of the time is spent here.
    //
    // Let $Z(X) = \prod_{i \in T} (X - \omega^i)$
    //
    // We handle a nasty edge case here: when doing N out of N interpolation, with N = 2^k, the batch
    // evaluation domain will have N roots of unity, but the degree of the accumulator poly will be
    // N as well which would require N + 1 roots of unity to do FFT.
    // This will trigger an error inside `accumulator_poly` when doing the last FFT-based
    // multiplication, which would require an FFT evaluation domain of size 2N which is not available.
    //
    // To fix this, we handle this case separately by splitting the accumulator poly into an `lhs`
    // of degree `N` which can be safely interpolated with `accumulator_poly` and an `rhs` of degree
    // 1. We then multiply the two together. We do not care about any performance implications of this
    // since we will never use N-out-of-N interpolation.
    //
    // We do this to avoid complicating our Lagrange coefficients API and our BatchEvaluationDomain
    // API (e.g., forbid N out of N Lagrange reconstruction by returning a `Result::Err`).
    if set.len() < dom.N() {
        accumulator_poly(&set, dom, FFT_THRESH)
    } else {
        // We handle |set| = 1 manually, since the `else` branch would yield an empty `lhs` vector
        // (i.e., a polynomial with zero coefficients) because `set` is empty after `pop()`'ing from
        // it. This makes `poly_mul_slow` bork, since it does not have clear semantics for this case.
        // TODO: Define polynomial multiplication semantics more carefully to avoid such issues.
        if set.len() == 1 {
            accumulator_poly(&set, dom, FFT_THRESH)
        } else {
            let last = set.pop().unwrap();

            let lhs = accumulator_poly(&set, dom, FFT_THRESH);
            let rhs = accumulator_poly(&[last], dom, FFT_THRESH);

            poly_mul_slow(&lhs, &rhs)
        }
    }
```

**File:** crates/aptos-crypto/src/blstrs/polynomials.rs (L447-455)
```rust
pub fn poly_differentiate(f: &mut Vec<Scalar>) {
    let f_deg = f.len() - 1;

    for i in 0..f_deg {
        f[i] = f[i + 1].mul(Scalar::from((i + 1) as u64));
    }

    f.truncate(f_deg);
}
```
