[1](#0-0) [2](#0-1)

### Citations

**File:** crates/aptos-dkg/src/pcs/shplonked.rs (L466-467)
```rust
    let c: E::ScalarField = trs.challenge_scalar();
    let c_powers = powers(c, n);
```

**File:** crates/aptos-dkg/src/pcs/shplonked.rs (L836-860)
```rust
    let h: usize = sigma_proof
        .z
        .hidden_evals
        .iter()
        .map(|v: &Vec<E::ScalarField>| v.len())
        .sum();
    let com_y_hom = shplonked_sigma::com_y_hom(&srs.taus_1[..h], srs.xi_1);
    // One weight per polynomial. Lagrange at x: evaluate cached basis polys at x (Horner).
    let (canonical, lagrange_cache) = build_lagrange_cache(&s_per_poly);
    let lagrange_at_x: Vec<Vec<E::ScalarField>> = (0..n)
        .map(|j| {
            let bases = lagrange_cache[canonical[j]].as_ref().unwrap();
            let n_rev = sets[j].rev.len();
            (0..sets[j].hid.len())
                .map(|i| bases[n_rev + i].evaluate(&x))
                .collect()
        })
        .collect();
    let g_rev_at_x = compute_g_rev::<E>(n, sets, &weights, &canonical, &lagrange_cache, x, y_rev);
    let eval_point_commit_hom = shplonked_sigma::EvalPointCommitHom::new(
        srs.taus_1[0],
        srs.xi_1,
        weights.clone(),
        lagrange_at_x,
    );
```
