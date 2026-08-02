[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/aptos-dkg/src/pcs/zeromorph.rs (L484-520)
```rust
        // q_scalars[k] for variable n-1-k. Pass point in reversed order so we get q_scalars[k] for variable k, matching quotients[k].
        let point_reversed_for_scalars: Vec<P::ScalarField> = point.iter().rev().cloned().collect();
        let (eval_scalar, (degree_check_q_scalars, zmpoly_q_scalars)): (
            P::ScalarField,
            (Vec<P::ScalarField>, Vec<P::ScalarField>),
        ) = eval_and_quotient_scalars::<P>(
            y_challenge,
            x_challenge,
            z_challenge,
            &point_reversed_for_scalars,
        );
        // f = z * poly.Z + q_hat + (-z * Φ_n(x) * e) + ∑_k (q_scalars_k * q_k)
        let mut f = UniPoly::from_coefficients_vec(poly.to_evaluations());
        f = f * z_challenge;
        f += &q_hat;
        f[0] += eval_scalar * eval;
        let q_scalars_for_s: Vec<P::ScalarField> = degree_check_q_scalars
            .iter()
            .zip(zmpoly_q_scalars.iter())
            .map(|(a, b)| *a + *b)
            .collect();
        let s_combined = r.0
            + z_challenge * s.0
            + q_scalars_for_s
                .iter()
                .zip(rs.iter())
                .map(|(scalar, rk)| *scalar * rk.0)
                .sum::<P::ScalarField>();

        quotients
            .into_iter()
            .zip(degree_check_q_scalars)
            .zip(zmpoly_q_scalars)
            .for_each(|((mut q, degree_check_scalar), zm_poly_scalar)| {
                q = q * (degree_check_scalar + zm_poly_scalar);
                f += &q;
            });
```

**File:** crates/aptos-dkg/src/pcs/zeromorph.rs (L750-761)
```rust
    fn verify(
        vk: &Self::VerificationKey,
        com: impl Into<Self::CommitmentNormalised>,
        challenge: Vec<Self::WitnessField>,
        eval: Self::WitnessField,
        proof: Self::OpeningProof,
        trs: &mut merlin::Transcript,
        batch: bool,
    ) -> anyhow::Result<()> {
        let com = com.into();
        Zeromorph::verify(&vk, &com.0, &challenge, &eval, &proof, trs, batch)
    }
```
