No vulnerability found for this question.

The premise is incorrect. Domain/context binding for DKG epoch replay protection is not implemented through `EncryptPubKey` derivation (`to()`), it's implemented through a separate `aux`/`session_id` value that is hashed into the PVSS transcript's proof-of-knowledge (SoK) and BLS-signed contribution.

Key facts:

1. `EncryptPubKey::to()` in `crates/aptos-dkg/src/pvss/encryption_dlog.rs` and `crates/aptos-dkg/src/pvss/chunky/keys.rs` derives a validator's persistent encryption key from its BLS decryption/consensus key. It is intentionally epoch-independent — this is by design since `eks` are reused as long as a validator's consensus key doesn't rotate, as seen in `build_dkg_pvss_config`, which derives `eks` directly from the target validators' persistent `bls12381::PublicKey` without any epoch mixed in. [1](#0-0) 

2. The actual epoch/session binding happens via the `aux` parameter passed separately into `deal()`/`verify()`. For `RealDKG`, this is `(dealer_epoch, dealer_address)`, constructed fresh both at dealing time and at verification time from the metadata/pub_params currently in scope: [2](#0-1) [3](#0-2) 

3. This `aux` is what gets bound into the BLS-signed contribution and verified via `batch_verify_soks`/`sig.verify_aggregate`, and separately into the chunky PVSS's `SokContext` (Fiat-Shamir transcript for the sigma-protocol SoK): [4](#0-3) [5](#0-4) 

4. `process_dkg_result_inner` builds `pub_params` (which determines `aux` at verify time) from `in_progress_session_state.metadata`, i.e., the epoch actually stored in on-chain in-progress DKG state, not from attacker-controlled input: [6](#0-5) 

Consequently, replaying an epoch-N transcript against epoch-(N+1) public params changes `aux` from `(N, addr)` to `(N+1, addr)`. Even though `eks` are identical across both `DKGSessionMetadata` instances (as they're derived from the same persistent validator keys), the SoK/BLS-signature verification in `verify_transcript` will fail because the transcript's proof/signature was computed over `aux=(N, addr)` while verification recomputes and checks against `aux=(N+1, addr)`. This causes `verify_transcript` (called via `DefaultDKG::verify_transcript` / `WTrx::verify` / chunky `Transcript::verify`) to correctly reject the stale transcript, so `verify_transcript` *does* distinguish the two epochs — contrary to the exploit's premise. There is no path by which unprivileged input can make a stale, epoch-N-bound transcript pass verification for epoch N+1 or corrupt `DKGSessionState`'s committed write set.

### Citations

**File:** types/src/dkg/real_dkg/mod.rs (L120-128)
```rust
    let validator_consensus_keys: Vec<bls12381::PublicKey> = next_validators
        .iter()
        .map(|vi| vi.public_key.clone())
        .collect();

    let consensus_keys: Vec<EncPK> = validator_consensus_keys
        .iter()
        .map(|k| k.to_bytes().as_slice().try_into().unwrap())
        .collect::<Vec<_>>();
```

**File:** types/src/dkg/real_dkg/mod.rs (L246-260)
```rust
        let my_index = my_index as usize;
        let my_addr = pub_params.session_metadata.dealer_validator_set[my_index].addr;
        let aux = (pub_params.session_metadata.dealer_epoch, my_addr);

        let wtrx = WTrx::deal(
            &pub_params.pvss_config.wconfig,
            &pub_params.pvss_config.pp,
            sk,
            pk,
            &pub_params.pvss_config.eks,
            input_secret,
            &aux,
            &Player { id: my_index },
            rng,
        );
```

**File:** types/src/dkg/real_dkg/mod.rs (L338-349)
```rust
        let aux = dealers_addresses
            .iter()
            .map(|address| (params.pvss_config.epoch, address))
            .collect::<Vec<_>>();

        trx.main.verify(
            &params.pvss_config.wconfig,
            &params.pvss_config.pp,
            &spks,
            &all_eks,
            &aux,
        )?;
```

**File:** crates/aptos-dkg/src/pvss/contribution.rs (L78-103)
```rust
    // Second, the signatures
    let msgs = soks
        .iter()
        .zip(aux)
        .map(|((player, comm, _, _), aux)| Contribution::<Gr, A> {
            comm: *comm,
            player: *player,
            aux: aux.clone(),
        })
        .collect::<Vec<Contribution<Gr, A>>>();
    let msgs_refs = msgs
        .iter()
        .map(|c| c)
        .collect::<Vec<&Contribution<Gr, A>>>();
    let pks = spks
        .iter()
        .map(|pk| pk)
        .collect::<Vec<&bls12381::PublicKey>>();
    let sig = bls12381::Signature::aggregate(
        soks.iter()
            .map(|(_, _, sig, _)| sig.clone())
            .collect::<Vec<bls12381::Signature>>(),
    )?;

    sig.verify_aggregate(&msgs_refs[..], &pks[..])?;
    Ok(())
```

**File:** crates/aptos-dkg/src/pvss/chunky/verify_common.rs (L17-50)
```rust
/// Context hashed into the SoK Fiat–Shamir transcript (dealer key, session, DST).
#[derive(Serialize, Clone, Debug)]
pub struct SokContext<'a, A: Serialize + Clone> {
    pub signing_pubkey: bls12381::PublicKey,
    pub session_id: &'a A,
    pub dealer_id: usize,
    pub dst: Vec<u8>,
}

impl<'a, A: Serialize + Clone> SokContext<'a, A> {
    /// Builds a SoK context for the Fiat–Shamir transcript.
    ///
    /// This context is hashed into the transcript so that proofs are bound to the dealer's
    /// signing key, the session, and the domain-separation tag. It is used when verifying
    /// weighted chunky PVSS transcripts (v1 and v2).
    ///
    /// # Arguments
    /// * `signing_pubkey` - The dealer's BLS12-381 public key used for signing.
    /// * `session_id` - Session identifier; serialized and bound into the transcript.
    /// * `dealer_id` - Index of the dealer in the weighted config.
    /// * `dst` - Domain-separation tag (DST) for the proof system.
    pub fn new(
        signing_pubkey: bls12381::PublicKey,
        session_id: &'a A,
        dealer_id: usize,
        dst: Vec<u8>,
    ) -> Self {
        Self {
            signing_pubkey,
            session_id,
            dealer_id,
            dst,
        }
    }
```

**File:** aptos-move/aptos-vm/src/validator_txns/dkg.rs (L99-116)
```rust
        let DKGState { in_progress, .. } = dkg_state;
        let in_progress_session_state =
            in_progress.ok_or(Expected(MissingResourceInprogressDKGSession))?;

        // Check epoch number.
        if dkg_node.metadata.epoch != config_resource.epoch() {
            return Err(Expected(EpochNotCurrent));
        }

        // Deserialize transcript and verify it.
        let pub_params = DefaultDKG::new_public_params(&in_progress_session_state.metadata);
        let transcript = bcs::from_bytes::<<DefaultDKG as DKGTrait>::Transcript>(
            dkg_node.transcript_bytes.as_slice(),
        )
        .map_err(|_| Expected(TranscriptDeserializationFailed))?;

        DefaultDKG::verify_transcript(&pub_params, &transcript)
            .map_err(|_| Expected(TranscriptVerificationFailed))?;
```
