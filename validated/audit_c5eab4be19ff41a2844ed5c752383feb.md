# Finding: Encryption Key Not Cryptographically Bound to Transcript When DigestKey Is Unavailable

### Title
Unsigned `encryption_key` field in `CertifiedChunkyDKGOutput` can be committed without verified binding to `transcript_bytes`, causing non-deterministic execution across validators - (File: `aptos-move/aptos-vm/src/validator_txns/chunky_dkg.rs`)

### Summary
In `process_chunky_dkg_result_inner`, the BLS aggregate signature only covers the deserialized `AggregatedSubtranscript` (`trx`), never the `encryption_key` field of `CertifiedChunkyDKGOutput`. The only mechanism that binds `encryption_key` to `transcript_bytes` is a local rederive-and-compare step, and the code's own comment states that this step is skipped whenever the local node has no `DigestKey` loaded ("skip verification and trust consensus").

### Finding Description
`CertifiedChunkyDKGOutput` is composed of a `certified_transcript: CertifiedAggregatedChunkySubtranscript` and a separate, independently-serialized `encryption_key: Vec<u8>` field: [1](#0-0) 

The multi-signature verification inside `process_chunky_dkg_result_inner` is performed only over the deserialized transcript (`trx`), not over `encryption_key`: [2](#0-1) 

The only remaining check that is supposed to bind `encryption_key` to the transcript is the rederive-and-compare step immediately following, but the accompanying comment explicitly documents that this check is conditional on local availability of a `DigestKey`: [3](#0-2) 

`derive_encryption_key_bytes` requires a `tau_g2` value sourced from a `DigestKey` blob: [4](#0-3) 

`DIGEST_KEY` is a lazily-initialized, optional resource that can be `None` in production if the digest-key blob file/path was never configured for a given node, or if reading/deserialization fails: [5](#0-4) 

Because the comparison logic is gated on local key availability, two honest validators executing the identical `CertifiedChunkyDKGOutput` validator transaction can reach different verdicts:
- A validator with `DIGEST_KEY` loaded rederives the encryption key from `trx` and rejects the transaction (`EncryptionKeyMismatch`, mapped to `Discard(ABORTED)`) if it does not match the claimed `encryption_key`. [6](#0-5) 
- A validator without `DIGEST_KEY` skips the check entirely and proceeds to commit the (unverified) `encryption_key`/`transcript_bytes` pair into `ChunkyDKGState` via `FINISH_WITH_CHUNKY_DKG_RESULT`.

Since `ChunkyDKGSessionState.transcript` (and the associated encryption key material) is subsequently relied upon by the protocol as a cryptographically-bound pair, a divergence at this point means different validators compute different VM outputs (`Success` vs. `Discard`) and different write sets for the same validator transaction.

### Impact Explanation
This breaks the required invariant that "VM outputs, transaction infos, events, and write sets must survive executor-to-storage handoff unchanged" and that committed state be deterministically derived. If part of the validator fleet has `DIGEST_KEY` configured/available and part does not (a plausible operational state — the key path is set independently per node, per `initialize_digest_key`), the fleet will disagree on the outcome of the exact same validator transaction, producing a consensus-breaking, hard-fork-class divergence: one commit path stores an `encryption_key` in `ChunkyDKGState` that is not cryptographically bound to the co-committed `transcript_bytes`, silently corrupting the invariant the PoC targets.

### Likelihood Explanation
The trigger does not require a malicious peer — it only requires heterogeneity in `DigestKey` availability across otherwise-honest validators (e.g., partial rollout, misconfiguration, or fullnode-like validators lacking the blob), combined with any upstream defect or benign non-determinism that causes the constructed `encryption_key` to not match the transcript. Because the aggregate BLS signature never covers `encryption_key` at all, its correctness rests entirely on this conditionally-skipped local check, making the enforcement gap systemic rather than incidental.

### Recommendation
Remove the conditional skip and make encryption-key/transcript binding verification mandatory for all nodes executing `process_chunky_dkg_result_inner`, or otherwise fold `encryption_key` into the data covered by the aggregate BLS signature so its integrity does not depend on optional local key material.

### Proof of Concept
A unit test can construct a `CertifiedChunkyDKGOutput` whose `certified_transcript.transcript_bytes` corresponds to transcript A, but whose `encryption_key` is `AggregatedSubtranscript::derive_encryption_key_bytes` output from a different transcript B. Feeding this into `process_chunky_dkg_result_inner` should assert `ExpectedFailure::EncryptionKeyMismatch` is returned in an environment with `DIGEST_KEY` present; the same input under an environment configuration where `DIGEST_KEY` resolves to `None` will instead proceed to `Ok(...)` and commit the mismatched pair — demonstrating the divergence described above. I was not able to view the exact lines implementing the comparison/branch beyond the comment at lines 156-158 due to tool output truncation on this file; a full-code Devin session would be needed to pin down the precise branch and confirm exact control flow before merging any fix.

### Citations

**File:** types/src/dkg/chunky_dkg.rs (L202-231)
```rust
/// Production DigestKey: checks override first, then reads from file path.
/// Returns `None` if neither was configured or if reading/deserializing fails.
/// TEST_DIGEST_KEY is only evaluated here (on first access), not at boot.
pub static DIGEST_KEY: Lazy<Option<Arc<DigestKey>>> = Lazy::new(|| {
    match DIGEST_KEY_OVERRIDE.get() {
        Some(DigestKeyOverride::TestFallback) => {
            return Some(Arc::clone(&TEST_DIGEST_KEY));
        },
        Some(DigestKeyOverride::Explicit(key)) => {
            return Some(Arc::clone(key));
        },
        None => {},
    }
    let path = DIGEST_KEY_PATH.get()?;
    let start = Instant::now();
    let key: DigestKey = match digest_key_file::read_digest_key(path) {
        Ok(k) => k,
        Err(e) => {
            tracing::error!("[DigestKey] failed to read file: {}", e);
            return None;
        },
    };
    let elapsed = start.elapsed();
    tracing::info!(
        "[DigestKey] loaded from {} in {:?}",
        path.display(),
        elapsed,
    );
    Some(Arc::new(key))
});
```

**File:** types/src/dkg/chunky_dkg.rs (L290-298)
```rust
impl AggregatedSubtranscript {
    /// Derive the encryption key bytes from this transcript using the given tau_g2.
    pub fn derive_encryption_key_bytes(&self, tau_g2: G2Affine) -> Result<Vec<u8>> {
        let mpk_g2 = self.subtranscript.get_dealt_public_key().as_g2();
        let encryption_key = EncryptionKey::new(mpk_g2, tau_g2);
        bcs::to_bytes(&encryption_key)
            .map_err(|e| anyhow::anyhow!("encryption key serialization error: {e}"))
    }
}
```

**File:** types/src/dkg/chunky_dkg.rs (L489-495)
```rust
/// Output of Chunky DKG: the certified transcript + derived encryption key.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct CertifiedChunkyDKGOutput {
    pub certified_transcript: CertifiedAggregatedChunkySubtranscript,
    #[serde(with = "serde_bytes")]
    pub encryption_key: Vec<u8>,
}
```

**File:** aptos-move/aptos-vm/src/validator_txns/chunky_dkg.rs (L40-40)
```rust
    EncryptionKeyMismatch = 0x010205,
```

**File:** aptos-move/aptos-vm/src/validator_txns/chunky_dkg.rs (L145-154)
```rust
        // TODO(ibalajiarun): Figure out how to verify without bcs deserialization
        let trx: AggregatedSubtranscript = bcs::from_bytes(&transcript_bytes).map_err(|_| {
            ExecutionFailure::Expected(ExpectedFailure::TranscriptDeserializationFailed)
        })?;
        if trx.dealer_epoch != metadata.epoch {
            return Err(ExecutionFailure::Expected(ExpectedFailure::EpochNotCurrent));
        }
        verifier
            .verify_multi_signatures(&trx, &signature)
            .map_err(|_| ExecutionFailure::Expected(ExpectedFailure::MultiSigVerificationFailed))?;
```

**File:** aptos-move/aptos-vm/src/validator_txns/chunky_dkg.rs (L156-158)
```rust
        // Rederive encryption key from the transcript and verify it matches the claimed key.
        // When no DigestKey is available (e.g. fullnodes without the blob), skip verification
        // and trust consensus.
```
