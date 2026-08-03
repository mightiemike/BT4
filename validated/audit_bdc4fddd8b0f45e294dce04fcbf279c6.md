## Title
Missing subgroup validation in `Subtranscript<E>`/`Transcript<E>` deserialization allows torsion-point injection into `Cs`/`Rs`/`Vs` - ([File: crates/aptos-dkg/src/pvss/chunky/subtranscript.rs] / [File: crates/aptos-crypto/src/arkworks/serialization.rs])

### Summary
`Transcript<E>::try_from(bytes)` for the chunky weighted PVSS transcript deserializes all group-element fields (`Vs`, `Cs`, `Rs` in `Subtranscript<E>`, and the SoK/range-proof components) through the shared helper `ark_de`, which explicitly calls arkworks' `CanonicalDeserialize::deserialize_with_mode(..., Validate::No)`. [1](#0-0) 
`Validate::No` skips the prime-order-subgroup membership check that arkworks otherwise performs, so a maliciously crafted `Transcript` byte-string can encode `G1`/`G2` points that are on the curve (or on its twist) but lie in a small-order torsion component instead of the intended prime-order subgroup. [2](#0-1) 

### Finding Description
This directly contradicts the project's own documented safety invariant: the `aptos-dkg` README states "The G1/G2 group elements in `blstrs` are deserialized safely via calls to `from_[un]compressed` rather than calls to `from_[un]compressed_unchecked` which does not check prime-order subgroup membership" — but this guarantee only applies to the `blstrs`-based DAS/unweighted transcripts (which call `bcs::from_bytes` directly on `blstrs` types whose `serde` impl performs the check), not to the arkworks-based chunky weighted transcript, which uses `ark_de` with `Validate::No`.

`Subtranscript::Cs`, `Rs`, and `Vs` are exactly the fields the exploit question names, and they are deserialized with `#[serde(deserialize_with = "ark_de")]`, inheriting the unchecked path. [3](#0-2) 
The same `ark_de` helper is also used for `subtrs` and `sharing_proof` (containing the SoK) at the `Transcript<E>` level, so the SoK components inherit the same lack of validation. [4](#0-3) 

Because `TryFrom<&[u8]> for Transcript<E>` is a thin wrapper around `bcs::from_bytes`, which recursively invokes these `deserialize_with` functions, no additional subgroup check occurs anywhere in the deserialization pipeline before `verify()` is invoked. [5](#0-4) 

The verifier's final check is a single (multi-)pairing equation that must equal the pairing identity, e.g. in v1's `verify()`: [6](#0-5) 
Pairing-based equality checks of the form `e(A,B) = e(C,D)` (or `multi_pairing(...) == 1`) are only sound as membership/consistency proofs when all inputs are confirmed to lie in the correct prime-order subgroups. If an attacker injects a small-order (torsion) point into one of the `G1Affine`/`G2Affine` slots feeding this multi-pairing/MSM, the bilinear pairing of a torsion element with anything can degenerate (e.g., pairing a point in the small-order component with a scalar multiple can produce values unrelated to the honestly-committed exponents, or in the worst case cancel to the identity independent of the actual committed shares), letting a dealer satisfy the "correctness of encryption/commitment" equation without the `Cs`/`Rs`/`Vs` values actually being consistent with the same underlying secret shares.

### Impact Explanation
This affects the DKG/PVSS transcript verification path, which is the mechanism validators use to establish trust in weighted secret-shared committee keys (e.g., for randomness generation or other DKG-consuming subsystems). If a corrupted/torsion-laden `Transcript` passes `verify()`, the shares recovered by honest parties from `Cs`/`Rs` may not correspond to the committed `Vs`/`V0` public shares, silently corrupting the dealt secret material without detection — a proof-integrity break in a system whose entire purpose is a zero-knowledge integrity guarantee over committed shares. This matches the "corrupt proof material" impact category in scope.

### Likelihood Explanation
**Caveat on production applicability:** the question's target file (`weighted_transcript.rs`, "v1") is, per the code, the scheme intended for production use (per `crates/aptos-dkg/README.md`, v2 is explicitly annotated "*Not used in production.*"), and v1's `Subtranscript<E>` (shared by both v1 and v2) is what carries `Cs`/`Rs`/`Vs` and is deserialized via the same vulnerable `ark_de` path. I could not fully load `weighted_transcript.rs`'s `verify()` body in this session (the file's content did not render fully via read_file — some ranges came back empty), so I cannot confirm with 100% certainty whether v1's specific pairing construction is exploitable via torsion-point cancellation (this requires deeper cryptanalysis of the exact MSM/pairing terms), only that the deserialization layer that feeds it provides **no defense whatsoever** against non-subgroup elements. This is a structural gap independent of the specific pairing equation's resistance, and it is plausible but unverified from available context whether the actual field arithmetic (Fp/Fr scalar rerandomization via `random_scalars`/Fiat-Shamir challenges in `verify()`) coincidentally provides some resistance to torsion injection. Given the ambiguity, I flag this as a **credible but not fully proven** vulnerability requiring further analysis of the exact pairing terms in v1's `verify()`.

### Recommendation
Change `ark_de` (or add a dedicated variant used exclusively by transcript/proof deserialization) to use `Validate::Yes` instead of `Validate::No`, ensuring `CanonicalDeserialize` performs both on-curve and prime-order-subgroup checks for every `G1Affine`/`G2Affine` embedded in `Subtranscript<E>`, `SharingProof<E>`, and any nested SoK/range-proof structures. At minimum, add an explicit post-deserialization pass in `TryFrom<&[u8]> for Transcript<E>` that calls `.is_in_correct_subgroup_assuming_on_curve()` (or equivalent) on every group element in `Cs`, `Rs`, `Vs`, `V0`, and all SoK/range-proof commitments before returning `Ok`, mirroring the guarantee already documented as required in the `aptos-dkg` README.

### Proof of Concept
Conceptual PoC (not independently executed in this session, since it requires cryptographic point construction with arkworks types not available via static analysis):
1. Use `ark_bls12_381::G1Projective`/`G2Projective` (or the appropriate curve used to instantiate `E`) to construct a point that lies on the curve but outside the prime-order subgroup (e.g., a point in the cofactor-torsion component, obtainable by hashing to the curve without clearing the cofactor, or by taking a small-order point on the twist).
2. Serialize this point with `CanonicalSerialize::serialize_with_mode(..., Compress::Yes)` to get raw compressed bytes.
3. Splice these bytes into the BCS encoding of a legitimately-dealt `Transcript<E>` at the byte offset corresponding to one entry of `subtrs.Cs` (or `Rs`/`Vs`).
4. Call `Transcript::<E>::try_from(&spliced_bytes)` — per `ark_de`'s use of `Validate::No`, this succeeds without error. [7](#0-6) 
5. Feed the resulting `Transcript` to `verify()` and check whether the multi-pairing equation still evaluates to the pairing identity; this final step needs to be executed with the concrete `E` and torsion point to conclusively determine exploitability of v1's exact equation, which I was unable to fully verify from the available file excerpts.

### Citations

**File:** crates/aptos-crypto/src/arkworks/serialization.rs (L29-38)
```rust
/// This function allows Arkworks types to be deserialized from Serde-compatible data sources.
/// It assumes the data was serialized with compression, and attempts to check its correctness.
pub fn ark_de<'de, D, A: CanonicalDeserialize>(data: D) -> Result<A, D::Error>
where
    D: serde::de::Deserializer<'de>,
{
    let s: Bytes = serde::de::Deserialize::deserialize(data)?;
    let a = A::deserialize_with_mode(s.reader(), Compress::Yes, Validate::No);
    a.map_err(serde::de::Error::custom)
}
```

**File:** crates/aptos-dkg/src/pvss/chunky/subtranscript.rs (L43-60)
```rust
pub struct Subtranscript<E: Pairing> {
    /// The dealt public key.
    /// ArkSize(E=Bls12_381): 96.
    #[serde(serialize_with = "ark_se", deserialize_with = "ark_de")]
    pub V0: E::G2Affine,
    /// The dealt public key shares.
    /// ArkSize(E=Bls12_381): 8 + 8·n + 96·W.
    #[serde(serialize_with = "ark_se", deserialize_with = "ark_de")]
    pub Vs: Vec<Vec<E::G2Affine>>,
    /// First chunked ElGamal component: C[i][j] = s_{i,j} * G + r_j * ek_i. Here s_i = \sum_j s_{i,j} * B^j // TODO: change notation because B is not a group element? maybe β or radix?
    /// ArkSize(E=Bls12_381): 8 + 8·n + 8·W + 48·W·c.
    #[serde(serialize_with = "ark_se", deserialize_with = "ark_de")]
    pub Cs: Vec<Vec<Vec<E::G1Affine>>>,
    /// Second chunked ElGamal component: R[j] = r_j * H.
    /// ArkSize(E=Bls12_381): 8 + 8·max_w + 48·max_w·c.
    #[serde(serialize_with = "ark_se", deserialize_with = "ark_de")]
    pub Rs: Vec<Vec<E::G1Affine>>,
}
```

**File:** crates/aptos-dkg/src/pvss/chunky/weighted_transcript_v2.rs (L60-69)
```rust
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
pub struct Transcript<E: Pairing> {
    dealer: Player,
    /// This is the aggregatable subtranscript
    #[serde(serialize_with = "ark_se", deserialize_with = "ark_de")]
    pub subtrs: Subtranscript<E>,
    /// Proof (of knowledge) showing that the s_{i,j}'s in C are base-B representations (of the s_i's in V, but this is not part of the proof), and that the r_j's in R are used in C
    #[serde(serialize_with = "ark_se", deserialize_with = "ark_de")]
    pub sharing_proof: SharingProof<E>,
}
```

**File:** crates/aptos-dkg/src/pvss/chunky/weighted_transcript_v2.rs (L93-99)
```rust
impl<E: Pairing> TryFrom<&[u8]> for Transcript<E> {
    type Error = CryptoMaterialError;

    fn try_from(bytes: &[u8]) -> Result<Self, Self::Error> {
        bcs::from_bytes::<Transcript<E>>(bytes)
            .map_err(|_| CryptoMaterialError::DeserializationError)
    }
```

**File:** crates/aptos-dkg/src/pvss/chunky/weighted_transcript.rs (L458-472)
```rust
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
