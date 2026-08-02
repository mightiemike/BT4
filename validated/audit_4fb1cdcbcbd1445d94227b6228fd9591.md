[1](#0-0) [2](#0-1)

### Citations

**File:** crates/aptos-dkg/src/pvss/chunky/weighted_transcript_v2.rs (L245-271)
```rust
        // Group Vs by player (convert flat Vec<E::G2> to Vec<Vec<E::G2>>)
        // Vs_flat is the inner Vec<E::G2> from CodomainShape
        let Vs: Vec<Vec<E::G2Affine>> = sc.group_by_player(&Vs_flat);

        // Generate the batch range proof, given the `range_proof_commitment` produced in the PoK
        let range_proof_projective = dekart_univariate_v2::Proof::prove(
            &pp.pk_range_proof,
            &f_evals_chunked_flat,
            pp.ell,
            &univariate_hiding_kzg::CommitmentNormalised(range_proof_commitment.0.clone()),
            &witness.hkzg_randomness,
            rng,
        );

        // Assemble the sharing proof
        let sharing_proof = SharingProof {
            SoK,
            range_proof: range_proof_projective.into(), // Doing G1 normalisation here
            range_proof_commitment: univariate_hiding_kzg::CommitmentNormalised(
                range_proof_commitment.0.clone(),
            ),
        };

        // Vs_flat from homomorphism codomain was grouped by player into Vs above.

        Ok((Cs, Rs, Vs, sharing_proof))
    }
```

**File:** crates/aptos-dkg/src/pvss/chunky/weighted_transcript_v2.rs (L317-326)
```rust
        // Do the SCRAPE LDT
        let ldt = LowDegreeTest::random(
            rng,
            sc.get_threshold_weight(),
            sc.get_total_weight() + 1,
            true,
            &sc.get_threshold_config().domain,
        );
        let Vs_flat = self.subtrs.all_Vs_flat();
        let ldt_msm_terms = ldt.ldt_msm_input(&Vs_flat)?;
```
