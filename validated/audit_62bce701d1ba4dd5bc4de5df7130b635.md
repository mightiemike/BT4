Based on the code reviewed, this is a valid finding, though the impact must be scoped correctly.

### Title
Chunky DKG `Transcript<E>::try_from` skips curve/subgroup validation via `ark_de(..., Validate::No)` - ([File: crates/aptos-dkg/src/pvss/chunky/weighted_transcript_v2.rs])

### Summary
`Transcript<E>::try_from<&[u8]>` in the chunky PVSS transcript path deserializes the embedded `Subtranscript<E>` (and `SharingProof<E>`) using the generic `ark_de` helper, which explicitly passes `Validate::No` to arkworks' `deserialize_with_mode`. This skips both on-curve and prime-order-subgroup checks for every `G1Affine`/`G2Affine` inside `Subtranscript.Vs`/`Cs`/`Rs`/`V0`.

### Finding Description
`Transcript<E>::try_from` simply calls `bcs::from_bytes::<Transcript<E>>(bytes)`: [1](#0-0) 

The `subtrs` field is annotated with `#[serde(serialize_with = "ark_se", deserialize_with = "ark_de")]`: [2](#0-1) 

`ark_de` deserializes using `Compress::Yes, Validate::No`: [3](#0-2) 

`Validate::No` in arkworks' `CanonicalDeserialize` disables both the on-curve check and the subgroup-membership check for affine points — it is meant as a performance escape hatch for contexts where inputs are already trusted. This contrasts with the `das` (non-chunky) PVSS `Transcript`, which relies on `blstrs`'s serde impl that always performs subgroup checks, as documented in the crate's own README: [4](#0-3) [5](#0-4) 

So the exploit hypothesis is confirmed at the deserialization layer: a byte blob crafted to contain off-curve or non-subgroup `G1Affine`/`G2Affine` values inside `Subtranscript.Vs`/`Cs` will pass `Transcript::<E>::try_from` without error, because `Validate::No` is used unconditionally, not conditionally on trust level of the input.

### Impact Explanation
The scope rules require the path to originate from **unprivileged** input and lead to **corruption of committed ledger state, proof material, or authenticated responses**. Two important caveats limit this to a lower-severity/likely-out-of-scope finding rather than a critical one:

1. `weighted_transcript_v2.rs` is explicitly documented as **not used in production**: [6](#0-5) 
This means the specific `try_from` cited in the question does not sit on any live commit/storage path today.

2. Even for the transcript variant that *is* used, DKG transcripts are aggregated/verified by validators before being embedded in a `ValidatorTransaction` and committed as system state (see `dkg/src/chunky/dkg_manager/mod.rs`, `dkg/src/transcript_aggregation/mod.rs`, `types/src/dkg/chunky_dkg.rs`). Malformed points that pass `try_from` would still need to survive the transcript's own SoK/range-proof/pairing verification logic to be accepted by validators and committed. I was not able to fully trace whether that downstream verification (`verify_weighted_preamble`, `SharingProof` checks, batched sigma-protocol verification) implicitly enforces subgroup membership as a side effect of its arithmetic (e.g., via MSM operations that would reject non-subgroup points, or via pairing checks that would produce garbage results silently) — this needs deeper investigation than the index allows.

If downstream verification does **not** independently re-check subgroup membership before accepting/aggregating a transcript, a malicious dealer (an unprivileged DKG participant) could submit a non-canonical transcript that gets persisted as a `DKGTranscript`/`ValidatorTransaction` payload, corrupting the on-chain DKG state and potentially making the resulting randomness/keys unusable or ambiguous — matching the "unusable or ambiguous on-chain DKG result" impact class the question describes.

### Likelihood Explanation
Moderate-to-low. The `try_from` gap is real and unconditional, but:
- It affects `weighted_transcript_v2.rs`, marked not-production, so the primary cited file is out of scope for mainnet impact.
- The same `Validate::No` pattern also exists in `weighted_transcript.rs` (v1, potentially production-used) and `subtranscript.rs`, which raises the same concern for the actually-deployed code path, but requires confirming that transcript verification (SoK/range-proof/pairing checks) doesn't reject invalid points before commit.
- DKG transcript submission also goes through validator-transaction consensus and aggregation logic (`dkg/src/chunky/agg_subtrx_producer.rs`, `transcript_aggregation`), which may impose additional structural/verification gates before anything is durably committed.

### Recommendation
- Confirm whether `weighted_transcript.rs` (v1, production) has the same `ark_de`/`Validate::No` pattern, and if so, either switch to `Validate::Yes` for untrusted-input deserialization paths, or ensure `Transcript::verify` (not just `try_from`) unconditionally performs subgroup-membership checks on every point in `Subtranscript` before any transcript is aggregated or persisted.
- Audit all callers of `ark_de` that handle unprivileged/network-supplied bytes and ensure `Validate::No` is used only in explicitly trust-verified contexts.

### Proof of Concept
Not fully verified end-to-end (would require exercising the downstream `verify`/aggregation code, which was not confirmed to reject non-subgroup points independently). At the `try_from` layer alone: constructing BCS bytes for a `Subtranscript<E>` where a `G1Affine`/`G2Affine` coordinate pair satisfies the byte-length/flag requirements of arkworks' compressed format but corresponds to a point outside the prime-order subgroup, then calling `Transcript::<E>::try_from(bytes)`, will succeed without error because `ark_de` uses `Validate::No`. [7](#0-6)

### Citations

**File:** crates/aptos-dkg/src/pvss/chunky/weighted_transcript_v2.rs (L61-69)
```rust
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

**File:** crates/aptos-crypto/src/arkworks/serialization.rs (L27-38)
```rust
/// Deserializes a type implementing `CanonicalDeserialize` from bytes produced by [`ark_se`].
///
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

**File:** crates/aptos-dkg/README.md (L37-40)
```markdown
- **v1** (`weighted_transcript.rs`): Verifier uses one pairing equation (one G1 MSM, one G2 MSM).
- **v2** (`weighted_transcript_v2.rs`): Verifier uses pairings only indirectly (e.g. in range proof), so might be used with a different range proof over a pairingless curve in the distant future. *Not used in production.*

Public types: `UnsignedWeightedTranscript`, `UnsignedWeightedTranscriptv2`, and signed variants `SignedWeightedTranscript`, `SignedWeightedTranscriptv2` (via `pvss::signed::GenericSigning`).
```

**File:** crates/aptos-dkg/README.md (L72-76)
```markdown
**Serialization & safety**  
We (mostly) rely on the `aptos-crypto` `SerializeKey` and `DeserializeKey` derives for safety during deserialization.
Specifically, each cryptographic object (e.g., public key, public parameters, etc) must implement `ValidCryptoMaterial` for serialization and `TryFrom` for deserialization when these derives are used.

The G1/G2 group elements in `blstrs` are deserialized safely via calls to `from_[un]compressed` rather than calls to `from_[un]compressed_unchecked` which does not check prime-order subgroup membership.
```

**File:** crates/aptos-dkg/src/pvss/das/unweighted_protocol.rs (L73-81)
```rust
impl TryFrom<&[u8]> for Transcript {
    type Error = CryptoMaterialError;

    fn try_from(bytes: &[u8]) -> Result<Self, Self::Error> {
        // NOTE: The `serde` implementation in `blstrs` already performs the necessary point validation
        // by ultimately calling `GroupEncoding::from_bytes`.
        bcs::from_bytes::<Transcript>(bytes).map_err(|_| CryptoMaterialError::DeserializationError)
    }
}
```
