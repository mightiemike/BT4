Based on my research, I found a legitimate consensus-relevant vulnerability analogous to the HPKE report's "error out on x25519 0 keys" and VRF counter/overflow bug classes: a missing small-order/identity-point check on the registered VRF public key, asymmetric with the check that already exists for the VRF proof's `Gamma` point.

### Title
Missing small-order check on registered VRF public keys allows VRF-seed grinding via a degenerate leader key - (File: stacks-common/src/util/vrf.rs)

### Summary
`VRFPublicKey::from_bytes`/`VRF::verify` never reject a public key that decompresses to a low-order (e.g. identity) curve point, even though the sibling check exists for the proof's `Gamma` point. A miner can register such a degenerate VRF public key via an ordinary, unprivileged `LeaderKeyRegisterOp`, and thereafter compute VRF "proofs" without possessing any real secret and without following the deterministic RFC8032/VRF nonce-derivation binding. This lets the registering miner grind the VRF seed that is fed into the next sortition, biasing selection of the next tenure's sortition winner.

### Finding Description
`VRFPublicKey::from_bytes` only decompresses the point and validates via `ed25519_dalek::VerifyingKey::from_bytes`; it performs no `is_small_order()` check: [1](#0-0) 

Contrast this with `VRFProof::from_slice`, which explicitly rejects a small-order `Gamma`: [2](#0-1) 

And `VRF::verify` only checks the proof's `Gamma` for small order, never the public key `Y_point`: [3](#0-2) 

This registered key comes directly from attacker/miner-controlled burnchain transaction data via `LeaderKeyRegisterOp::parse_data`, with no additional key-quality validation performed in `LeaderKeyRegisterOp::check`: [4](#0-3) 

If `Y_point` is the group identity `O` (a low-order point, corresponding to the "private key" `x = 0`), then in `verify()`, `c * Y_point_ed = O` for *any* `c`, so `U_point = s*B` is independent of `c`. This mirrors exactly what an honest `prove()` would compute for `x = 0`: `Gamma = 0*H = O`, and `s = k + c*0 = k` for any nonce `k`. That means *anyone* — not just the original registrant — can freely pick nonces `k`, compute `U = k*B`, `V = k*H`, derive `c = Hash(H, Gamma=O, U, V)`, and set `s = k`, producing a proof that always verifies. This breaks the VRF's core determinism/binding property that only the legitimate keyholder can produce exactly one valid proof per message.

The resulting proof feeds directly into the next sortition's VRF seed and the sortition index computation: [5](#0-4) [6](#0-5) 

and is enforced as consensus-critical during Nakamoto block validation (`check_normal_coinbase_tx`, `validate_vrf_seed`, `validate_burnchain`): [7](#0-6) [8](#0-7) 

Since `s` (and hence the resulting proof bytes and `VRFSeed::from_proof`) can be freely chosen by iterating nonces, the winning miner who registers this degenerate key can precompute many distinct, independently valid VRF proofs for the same fixed `alpha` (the prior sortition hash) and select whichever yields a `new_seed` that biases `sortition_hash.mix_VRF_seed(VRF_seed).to_uint256()` toward a favorable range in the *next* burn distribution — i.e., toward re-selecting themselves or a colluding candidate as the next sortition winner.

### Impact Explanation
This is a minority-triggerable divergence from the intended sortition/VRF security property: the VRF seed governing the next sortition's winner selection is supposed to be unpredictable and uniquely bound to the winning miner's committed key/proof. With a degenerate identity key, that binding disappears and the seed becomes attacker-grindable, letting a single miner bias which candidate wins subsequent sortitions — a "High" severity minority-triggerable sortition/VRF divergence that can be leveraged toward disproportionate block-reward capture.

### Likelihood Explanation
Registering a `LeaderKeyRegisterOp` is a normal, unprivileged burnchain operation open to any participant. Encoding the ed25519 identity point (`0x0100000...00`) as the VRF public key requires no special access, only a routine burnchain transaction, matching the "unprivileged, minority-triggerable" bar.

### Recommendation
Add a small-order/identity check to `VRFPublicKey::from_bytes` (mirroring the `is_small_order()` check already applied to `Gamma`), and reject public keys that decompress to a low-order point in `LeaderKeyRegisterOp::check`/`parse_data`, so that malicious or degenerate VRF keys cannot be registered or accepted for use in `VRF::verify`.

### Proof of Concept
1. Encode the ed25519 identity point as bytes (`y = 1`, sign bit 0: `01 00 00 ... 00`), and submit it as the `public_key` field of a `LeaderKeyRegisterOp` burnchain transaction — `LeaderKeyRegisterOp::parse_data` and `VRFPublicKey::from_bytes` accept it since no small-order check exists.
2. Once this key wins a sortition slot (via a normal burn commit), for the required `alpha = sortition_hash`, compute many candidate proofs: pick nonce `k`, set `Gamma = O`, `U = k*B`, `V = k*H`, `c = ed25519_scalar_from_hash128(hash_points(H, O, U, V))`, `s = k`.
3. Verify each candidate offline with `VRF::verify(identity_pubkey, proof, alpha)`, which succeeds for every such construction.
4. Compute `VRFSeed::from_proof(proof)` for each candidate and pick the one whose resulting `next_sortition_hash.mix_VRF_seed(seed)` lands in the desired range of the anticipated next burn distribution, then submit the corresponding block commit with that `new_seed`, biasing the next sortition winner.

### Citations

**File:** stacks-common/src/util/vrf.rs (L150-164)
```rust
    pub fn from_bytes(pubkey_bytes: &[u8]) -> Option<VRFPublicKey> {
        let pubkey_slice = pubkey_bytes.try_into().ok()?;

        // NOTE: `ed25519_dalek::VerifyingKey::from_bytes` docs say
        //  that this check must be performed by the caller, but as of
        //  latest, it actually performs the check as well. However,
        //  we do this check out of an abundance of caution because
        //  that's what the docs say to do!

        let checked_pubkey = CompressedEdwardsY(pubkey_slice);
        checked_pubkey.decompress()?;

        let key = ed25519_dalek::VerifyingKey::from_bytes(&pubkey_slice).ok()?;
        Some(VRFPublicKey(key))
    }
```

**File:** stacks-common/src/util/vrf.rs (L288-299)
```rust
                let gamma_opt = CompressedEdwardsY::from_slice(&bytes[0..32])
                    .ok()
                    .and_then(|y| y.decompress());
                if gamma_opt.is_none() {
                    test_debug!("Invalid Gamma");
                    return None;
                }
                let gamma = gamma_opt.unwrap();
                if gamma.is_small_order() {
                    test_debug!("Invalid Gamma -- small order");
                    return None;
                }
```

**File:** stacks-common/src/util/vrf.rs (L519-530)
```rust
    pub fn verify(Y_point: &VRFPublicKey, proof: &VRFProof, alpha: &[u8]) -> Result<bool, Error> {
        let H_point = VRF::hash_to_curve(Y_point, alpha);
        let s_reduced = proof.s();
        let Y_point_ed = CompressedEdwardsY(Y_point.to_bytes())
            .decompress()
            .ok_or(Error::InvalidPublicKey)?;
        if proof.Gamma().is_small_order() {
            return Err(Error::InvalidPublicKey);
        }

        let U_point = s_reduced * &ED25519_BASEPOINT_POINT - proof.c() * Y_point_ed;
        let V_point = s_reduced * &H_point - proof.c() * proof.Gamma();
```

**File:** stackslib/src/chainstate/burn/operations/leader_key_register.rs (L111-119)
```rust
        let consensus_hash = ConsensusHash::from_bytes(data.get(0..20)?)
            .expect("FATAL: invalid byte slice for consensus hash");
        let pubkey = match VRFPublicKey::from_bytes(data.get(20..52)?) {
            Some(pubk) => pubk,
            None => {
                warn!("Invalid VRF public key");
                return None;
            }
        };
```

**File:** stackslib/src/chainstate/burn/sortition.rs (L120-149)
```rust
    /// Given the weighted burns, VRF seed of the last winner, and sortition hash, pick the next
    /// winner.  Return the index into the distribution *if there is a sample to take*.
    fn sample_burn_distribution(
        dist: &[BurnSamplePoint],
        VRF_seed: &VRFSeed,
        sortition_hash: &SortitionHash,
    ) -> Option<usize> {
        if dist.is_empty() {
            // no winners
            return None;
        }
        if dist.len() == 1 {
            // only one winner
            return Some(0);
        }

        let index = sortition_hash.mix_VRF_seed(VRF_seed).to_uint256();
        for (i, dist_elem) in dist.iter().enumerate() {
            if (dist_elem.range_start <= index) && (index < dist_elem.range_end) {
                debug!(
                    "Sampled {}: i = {}, sortition index = {}",
                    dist_elem.candidate.block_header_hash, i, &index
                );
                return Some(i);
            }
        }

        // should never happen
        panic!("FATAL ERROR: unable to map {} to a range", index);
    }
```

**File:** stacks-common/src/types/chainstate.rs (L570-577)
```rust
    pub fn from_proof(proof: &VRFProof) -> VRFSeed {
        let h = Sha512Trunc256Sum::from_data(&proof.to_bytes());
        VRFSeed(h.0)
    }

    pub fn is_from_proof(&self, proof: &VRFProof) -> bool {
        self.as_bytes().to_vec() == VRFSeed::from_proof(proof).as_bytes().to_vec()
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1819-1853)
```rust
        if let Some(coinbase_tx) = self.get_coinbase_tx() {
            let (_, _, vrf_proof_opt) = coinbase_tx
                .try_as_coinbase()
                .expect("FATAL: `get_coinbase_tx()` did not return a coinbase");

            let vrf_proof = vrf_proof_opt.ok_or(ChainstateError::InvalidStacksBlock(
                "Nakamoto coinbase must have a VRF proof".into(),
            ))?;

            // this block's VRF proof must have been generated from the last sortition's sortition
            // hash (which includes the last commit's VRF seed)
            let valid = match VRF::verify(leader_vrf_key, vrf_proof, sortition_hash.as_bytes()) {
                Ok(v) => v,
                Err(e) => {
                    warn!(
                        "Invalid Stacks block header {}: failed to verify VRF proof: {}",
                        self.header.block_hash(),
                        e
                    );
                    false
                }
            };

            if !valid {
                warn!("Invalid Nakamoto block: leader VRF key did not produce a valid proof";
                    "consensus_hash" => %self.header.consensus_hash,
                    "stacks_block_hash" => %self.header.block_hash(),
                    "stacks_block_id" => %self.header.block_id(),
                    "leader_public_key" => %leader_vrf_key.to_hex(),
                    "sortition_hash" => %sortition_hash
                );
                return Err(ChainstateError::InvalidStacksBlock(
                    "Invalid Nakamoto block: leader VRF key did not produce a valid proof".into(),
                ));
            }
```

**File:** stackslib/src/chainstate/stacks/block.rs (L226-237)
```rust
        // this header's proof must hash to the burn chain tip's VRF seed
        if !block_commit.new_seed.is_from_proof(&self.proof) {
            let msg = format!(
                "Invalid Stacks block header {}: invalid VRF proof: hash({}) != {} (but {})",
                self.block_hash(),
                self.proof.to_hex(),
                block_commit.new_seed,
                VRFSeed::from_proof(&self.proof)
            );
            debug!("{}", msg);
            return Err(Error::InvalidStacksBlock(msg));
        }
```
