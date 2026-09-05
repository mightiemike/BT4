No vulnerability found for this question.

**Reasoning summary:** The premise is cryptographically false. `Secp256k1PublicKey::to_bytes_compressed()` calls `key.serialize()` [1](#0-0)  which produces exactly one canonical 33-byte compressed encoding for a given elliptic-curve point — there is no "differently-encoded" valid compressed serialization for the same public key. The `verify_low_s` flag in `recover_to_pubkey_possibly_with_low_s_verification` only toggles whether high-S signatures are rejected; it does not change what public key point is recovered [2](#0-1) . The repo's own test explicitly proves that a low-S and its negated (high-S) counterpart recover to the identical public key: `assert_eq!(recovered_from_orig, recovered_from_high_s, "both signatures should recover to the same public key")` [3](#0-2) .

Additionally, even if a signer somehow produced two signatures recovering to the same public key, `verify_signer_signatures` uses `signers_by_pk.remove(&public_key_bytes)` [4](#0-3) , which consumes the map entry on first use; a second signature recovering to the same bytes would find no entry and cause the whole block validation to fail with `InvalidStacksBlock` [5](#0-4) , not double-count the weight.

Since a single EC public key has one canonical compressed byte representation, and the map removal semantics prevent reuse of any single reward-set entry, the equality "distinct valid signer signatures" == "distinct signers in the reward set" is preserved. No path exists for an unprivileged signer to register two `(public_key_bytes, weight)` mappings for one physical key or double-count weight toward `total_weight_signed`.

### Citations

**File:** stacks-common/src/util/secp256k1/native.rs (L177-179)
```rust
    pub fn to_bytes_compressed(&self) -> Vec<u8> {
        self.key.serialize().to_vec()
    }
```

**File:** stacks-common/src/util/secp256k1/native.rs (L207-238)
```rust
    fn recover_to_pubkey_possibly_with_low_s_verification(
        msg: &[u8],
        sig: &MessageSignature,
        verify_low_s: bool,
    ) -> Result<Secp256k1PublicKey, &'static str> {
        _secp256k1.with(|ctx| {
            let msg = LibSecp256k1Message::from_slice(msg).map_err(|_e| {
                "Invalid message: failed to decode data hash: must be a 32-byte hash"
            })?;

            let secp256k1_sig = sig
                .to_secp256k1_recoverable()
                .ok_or("Invalid signature: failed to decode recoverable signature")?;

            if verify_low_s {
                let secp256k1_sig_standard = secp256k1_sig.to_standard();
                let mut secp256k1_sig_low_s = secp256k1_sig_standard;
                secp256k1_sig_low_s.normalize_s();
                if secp256k1_sig_low_s != secp256k1_sig_standard {
                    return Err("Invalid signature: high-S");
                }
            }

            let recovered_pubkey = ctx
                .recover_ecdsa(&msg, &secp256k1_sig)
                .map_err(|_e| "Invalid signature: failed to recover public key")?;

            Ok(Secp256k1PublicKey {
                key: recovered_pubkey,
                compressed: true,
            })
        })
```

**File:** stacks-common/src/util/secp256k1/native.rs (L828-831)
```rust
        assert_eq!(
            recovered_from_orig, recovered_from_high_s,
            "both signatures should recover to the same public key"
        );
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1148-1159)
```rust
            let (signer, signer_index) = signers_by_pk.remove(&public_key_bytes).ok_or_else(|| {
                warn!(
                    "Found an invalid public key. Reward set has {} signers. Chain length {}. Signatures length {}",
                    signers.len(),
                    self.chain_length,
                    self.signer_signature.len(),
                );
                ChainstateError::InvalidStacksBlock(format!(
                    "Public key {} not found in the reward set",
                    public_key.to_hex()
                ))
            })?;
```
