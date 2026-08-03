[1](#0-0) [2](#0-1)

### Citations

**File:** crates/aptos-dkg/src/sigma_protocol/proof.rs (L13-31)
```rust
#[derive(CanonicalSerialize, Debug, CanonicalDeserialize, Clone)]
pub struct Proof<F: Field, H: homomorphism::Trait>
where
    H::Domain: Witness<F>,
    H::CodomainNormalized: Statement,
{
    /// The “first item” recorded in the proof, which can be either:
    /// - the prover's commitment (H::Codomain)
    /// - the verifier's challenge (E::ScalarField)
    ///
    /// ArkSize(H=two_term_msm::Homomorphism<G1>): 49.
    /// ArkSize(H=hkzg_chunked_elgamal::Homomorphism): 65 + 8·(n + W + max_w) + 48·(W + max_w)·c.
    pub first_proof_item: FirstProofItem<F, H>,
    /// Prover's second message (response).
    ///
    /// ArkSize(F=Bls12_381::Fr, H=two_term_msm::Homomorphism<G1>): 64.
    /// ArkSize(F=Bls12_381::Fr, H=hkzg_chunked_elgamal::Homomorphism): 48 + 8·(n + W + max_w) + 32·(W + max_w)·c.
    pub z: H::Domain,
}
```

**File:** crates/aptos-dkg/src/sigma_protocol/proof.rs (L105-118)
```rust
// Manual implementation of PartialEq is required here because deriving PartialEq would
// automatically require `H` itself to implement PartialEq, which is undesirable.
impl<F: Field, H: homomorphism::Trait> PartialEq for FirstProofItem<F, H>
where
    H::CodomainNormalized: Statement,
{
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (FirstProofItem::Commitment(a), FirstProofItem::Commitment(b)) => a == b,
            (FirstProofItem::Challenge(a), FirstProofItem::Challenge(b)) => a == b,
            _ => false,
        }
    }
}
```
