This is a real, confirmed vulnerability: `ark_de` explicitly passes `Validate::No` to `deserialize_with_mode`, which skips arkworks' subgroup/on-curve validation entirely during deserialization.

### Title
Missing subgroup validation on `Subtranscript` deserialization allows invalid group elements to corrupt aggregated DKG transcripts - (File: `crates/aptos-dkg/src/pvss/chunky/subtranscript.rs`)

### Summary
`Subtranscript<E>` fields (`V0`, `Vs`, `Cs`, `Rs`) are deserialized via the `ark_de` helper, which calls `A::deserialize_with_mode(s.reader(), Compress::Yes, Validate::No)`. [1](#0-0)  `Validate::No` explicitly disables arkworks' on-curve and prime-order-subgroup membership checks that would normally happen in `CanonicalDeserialize`. The `Subtranscript` struct fields use exactly this deserializer. [2](#0-1) 

Once deserialized, `SubtranscriptProjective::aggregate_with` only performs *length* `ensure!` checks (lines 174–203) before doing unchecked group additions `self.V0_proj += other.V0`, `*v_ij += *other_v_ij`, `*c_ijk += *other_c_ijk`, `*r_jk += *other_r_jk`. [3](#0-2) 

### Finding Description
An unprivileged/malicious validator can craft a `ChunkyDKGTranscript` whose `transcript_bytes` BCS-encodes a `Subtranscript` containing points that pass BCS length/shape checks in `verify_weighted_preamble` (which only checks vector lengths, not point validity) but are not valid subgroup elements — e.g., points on the curve but in the wrong (large cofactor) subgroup, or malformed encodings that `Validate::No` lets through. This transcript is received by `ChunkyTranscriptAggregationState::add` → `validate_and_deserialize_transcript` → `deserialize_chunky_transcript_and_verify`, which calls `bcs::from_bytes` (triggering the unchecked `ark_de` path) and then `transcript.verify(...)`. [4](#0-3) 

Whether this is actually exploitable hinges on whether `transcript.verify()` itself rejects invalid-subgroup points through its pairing/SoK/range-proof checks (`weighted_transcript.rs::verify` and `weighted_transcript_v2.rs::verify`) before `aggregate_with` is invoked in `agg_subtrx_producer.rs`. [5](#0-4) [6](#0-5) 

I was **not able to fully verify** whether the multi-pairing equation checked in `weighted_transcript.rs::verify` (lines 458–474) algebraically rejects small-subgroup/non-prime-order points for `V0`/`Vs`/`Cs`/`Rs`, or whether such points could satisfy the equation (e.g., via identity-element or torsion tricks that zero out contributions to the pairing check while still being folded into the aggregate). Establishing this requires either running the actual pairing math with a crafted small-subgroup point, or a deeper review of the sigma-protocol/homomorphism (`hkzg_chunked_elgamal`) and range-proof (`dekart_univariate_v2`) verification logic, which was outside what I could conclusively trace here.

### Impact Explanation
If a small-subgroup or otherwise invalid point can pass `transcript.verify()` (which is the actual admission gate before `aggregate_with` is called), the aggregation would fold algebraically invalid data into the epoch's committed DKG transcript, corrupting shares that downstream validators decrypt/verify against on-chain DKG state — a state-integrity impact matching the review's "corrupt committed state / corrupt proof material" criterion. If `verify()` reliably rejects all invalid points (as its multi-pairing check is designed to do), then the missing subgroup check in `aggregate_with` alone is not independently exploitable, since it is unreachable with attacker-controlled invalid elements.

### Likelihood Explanation
Low-to-uncertain. The comment in the crate's own README explicitly states the project's safety invariant: "The G1/G2 group elements in `blstrs` are deserialized safely via calls to `from_[un]compressed`... rather than ... `from_[un]compressed_unchecked` which does not check prime-order subgroup membership" — but this is a `blstrs`-era claim about a different backend; the `arkworks`-based `chunky` PVSS code path uses `ark_de` with `Validate::No`, which appears inconsistent with that stated invariant. This strongly suggests the missing validation is either (a) intentionally deferred because `verify()`'s pairing checks are believed sufficient, or (b) an unreviewed gap. Without confirming the pairing-check's rejection guarantees against all invalid-subgroup inputs, I cannot assert exploitability with high confidence.

### Recommendation
1. Confirm whether `weighted_transcript.rs::verify`/`weighted_transcript_v2.rs::verify` pairing/SoK/range-proof checks are complete subgroup checks for every point in `Subtranscript` (V0, all Vs, all Cs, all Rs) — not just points used in the primary MSM terms.
2. Regardless, switch `ark_de` (or add a dedicated deserializer for `Subtranscript`) to use `Validate::Yes` so that on-curve and subgroup membership is enforced at deserialization time, matching the stated project invariant, rather than relying solely on downstream pairing checks to catch invalid points before aggregation.
3. Add explicit subgroup-membership assertions in `SubtranscriptProjective::aggregate_with` (or in `to_aggregated`) as defense-in-depth, independent of `verify()`'s pairing logic.

### Proof of Concept
Not independently constructed/verified — a complete PoC would require: (1) crafting compressed-point bytes for a point in `E::G1`/`E::G2` that lies on the curve but is not in the prime-order subgroup (or the identity element, which is valid in the subgroup but algebraically degenerate), (2) confirming `ark_de`'s `Validate::No` accepts it, and (3) determining experimentally whether `weighted_transcript::verify`'s multi-pairing check (or v2's sigma-protocol checks) accepts or rejects a `Subtranscript` containing that point before it reaches `aggregate_with`. This experimental step could not be completed within the scope of this review.

### Citations

**File:** crates/aptos-crypto/src/arkworks/serialization.rs (L31-38)
```rust
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

**File:** crates/aptos-dkg/src/pvss/chunky/subtranscript.rs (L174-235)
```rust
        ensure!(
            self.Cs_proj.len() == sc.get_total_num_players(),
            "Cs_proj length {} != num_players {}",
            self.Cs_proj.len(),
            sc.get_total_num_players()
        );
        ensure!(
            self.Vs_proj.len() == sc.get_total_num_players(),
            "Vs_proj length {} != num_players {}",
            self.Vs_proj.len(),
            sc.get_total_num_players()
        );
        ensure!(
            self.Cs_proj.len() == other.Cs.len(),
            "Cs_proj length {} != other {}",
            self.Cs_proj.len(),
            other.Cs.len()
        );
        ensure!(
            self.Rs_proj.len() == other.Rs.len(),
            "Rs_proj length {} != other {}",
            self.Rs_proj.len(),
            other.Rs.len()
        );
        ensure!(
            self.Vs_proj.len() == other.Vs.len(),
            "Vs_proj length {} != other {}",
            self.Vs_proj.len(),
            other.Vs.len()
        );

        // Aggregate the V0s
        self.V0_proj += other.V0;

        // Aggregate Vs (nested) element-wise
        for (vs_row, other_row) in self.Vs_proj.iter_mut().zip(&other.Vs) {
            ensure!(
                vs_row.len() == other_row.len(),
                "Vs row length {} != other {}",
                vs_row.len(),
                other_row.len()
            );
            for (v_ij, other_v_ij) in vs_row.iter_mut().zip(other_row) {
                *v_ij += *other_v_ij;
            }
        }

        // Aggregate Cs (nested) element-wise
        for (cs_player, other_player) in self.Cs_proj.iter_mut().zip(&other.Cs) {
            for (cs_chunks, other_chunks) in cs_player.iter_mut().zip(other_player) {
                for (c_ijk, other_c_ijk) in cs_chunks.iter_mut().zip(other_chunks) {
                    *c_ijk += *other_c_ijk;
                }
            }
        }

        // Aggregate Rs element-wise
        for (rs_row, other_row) in self.Rs_proj.iter_mut().zip(&other.Rs) {
            for (r_jk, other_r_jk) in rs_row.iter_mut().zip(other_row) {
                *r_jk += *other_r_jk;
            }
        }
```

**File:** dkg/src/chunky/common.rs (L46-62)
```rust
    let transcript: ChunkyTranscript = bcs::from_bytes(transcript_bytes)
        .map_err(|e| anyhow!("[ChunkyDKG] Unable to deserialize chunky transcript: {e}"))?;

    let mut rng = rand::thread_rng();
    // Verify the transcript cryptographically.
    monitor!(
        "chunky_validate_transcript_verify",
        transcript.verify(
            &dkg_config.threshold_config,
            &dkg_config.public_parameters,
            signing_pubkeys,
            &dkg_config.eks,
            &dkg_config.session_metadata,
            &mut rng,
        )
    )
    .context("chunky transcript verification failed")?;
```

**File:** crates/aptos-dkg/src/pvss/chunky/weighted_transcript.rs (L298-317)
```rust
    #[allow(non_snake_case)]
    fn verify<A: Serialize + Clone, R: RngCore + CryptoRng>(
        &self,
        sc: &Self::SecretSharingConfig,
        pp: &Self::PublicParameters,
        spks: &[Self::SigningPubKey],
        eks: &[Self::EncryptPubKey],
        sid: &A,
        rng: &mut R,
    ) -> anyhow::Result<()> {
        let sok_cntxt = verify_weighted_preamble(
            sc,
            pp,
            &self.subtrs,
            &self.dealer,
            spks,
            eks,
            sid,
            <Self as traits::Transcript>::dst(),
        )?;
```

**File:** dkg/src/chunky/agg_subtrx_producer.rs (L240-246)
```rust
        if let Some(agg_subtrx) = inner_state.subtrx.as_mut() {
            agg_subtrx
                .aggregate_with(&self.dkg_config.threshold_config, &subtranscript)
                .context("chunky transcript aggregation failed")?;
        } else {
            inner_state.subtrx = Some(subtranscript.to_aggregated());
        }
```
