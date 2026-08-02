[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/aptos-dkg/src/range_proofs/dekart_univariate_v2.rs (L1314-1332)
```rust
        fn msm_terms(
            &self,
            input: &Self::Domain,
        ) -> Result<Self::CodomainShape<MsmInput<Self::Base, Self::Scalar>>> {
            let mut scalars = Vec::with_capacity(2);
            scalars.push(input.poly_randomness.0);
            scalars.push(input.hiding_kzg_randomness.0);

            let mut bases = Vec::with_capacity(2);
            bases.push(self.base_1);
            bases.push(self.base_2);

            Ok(CodomainShape(MsmInput { bases, scalars }))
        }

        fn msm_eval(input: MsmInput<Self::Base, Self::Scalar>) -> Result<Self::MsmOutput> {
            C::msm(input.bases(), input.scalars())
                .map_err(|e| anyhow!("MSM failed: length mismatch (min length {})", e))
        }
```

**File:** crates/aptos-dkg/src/sigma_protocol/homomorphism/mod.rs (L10-11)
```rust
pub mod fixed_base_msms;
pub mod tuple;
```

**File:** crates/aptos-dkg/src/sigma_protocol/traits.rs (L258-290)
```rust
    fn msm_terms_for_verify<Ct: Serialize, H2>(
        &self,
        public_statement: &Self::CodomainNormalized,
        proof: &Proof<<Self::Group as PrimeGroup>::ScalarField, H2>,
        cntxt: &Ct,
    ) -> Result<
        Vec<
            MsmInput<<Self::Group as CurveGroup>::Affine, <Self::Group as PrimeGroup>::ScalarField>,
        >,
    >
    where
        H2: homomorphism::Trait<
            Domain = Self::Domain,
            CodomainNormalized = Self::CodomainNormalized,
        >,
    {
        let prover_first_message = match proof.prover_commitment() {
            Some(m) => m,
            None => bail!("proof must contain commitment for Fiat–Shamir"),
        };

        let c = self.fiat_shamir_challenge_for_sigma_protocol(
            cntxt,
            public_statement,
            prover_first_message,
        );
        self.msm_terms_for_verify_with_challenge(
            public_statement,
            prover_first_message,
            &proof.z,
            c,
        )
    }
```
