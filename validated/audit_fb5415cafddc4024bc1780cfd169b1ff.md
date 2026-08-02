[1](#0-0)

### Citations

**File:** crates/aptos-dkg/src/pvss/chunky/weighted_transcript.rs (L455-472)
```rust
        let random_scalar_for_dekart: E::ScalarField = sample_field_element(rng);
        let random_scalar_for_ciphertext_check: E::ScalarField = sample_field_element(rng);

        let res = E::multi_pairing(
            dekart_verification_g1_terms
                .into_iter()
                .map(|g| (g * random_scalar_for_dekart).into_affine())
                .chain([
                    (combined_G1 * random_scalar_for_ciphertext_check).into_affine(),
                    (*pp.get_encryption_public_params().message_base()
                        * random_scalar_for_ciphertext_check)
                        .into_affine(),
                ]),
            dekart_verification_g2_terms
                .into_iter()
                .chain([pp.get_commitment_base(), (-combined_G2).into_affine()]),
        );
        if PairingOutput::<E>::ZERO != res {
```
