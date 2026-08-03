[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/aptos-dkg/src/range_proofs/dekart_univariate_v2.rs (L996-997)
```rust
        // Step 4b
        fiat_shamir::append_f_j_commitments::<E>(&mut fs_t, &Cs);
```

**File:** crates/aptos-dkg/src/range_proofs/dekart_univariate_v2.rs (L1020-1026)
```rust
        let U_bases: Vec<E::G1Affine> = {
            let mut v = Vec::with_capacity(2 + Cs.len());
            v.push(*hat_C);
            v.push(*D);
            v.extend_from_slice(&Cs);
            v
        };
```

**File:** crates/aptos-dkg/src/range_proofs/dekart_univariate_v2.rs (L1036-1041)
```rust
        let U = E::G1::msm(&U_bases, &U_scalars).map_err(|min_len| {
            anyhow!(
                "Failed to compute MSM in DeKARTv2 (bases/scalars min length: {})",
                min_len
            )
        })?;
```
