[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/aptos-crypto/src/secp256r1_ecdsa/secp256r1_ecdsa_sigs.rs (L36-45)
```rust
    /// Deserialize an P256Signature, without checking for malleability
    /// Uses the SEC1 serialization format.
    pub fn from_bytes_unchecked(
        bytes: &[u8],
    ) -> std::result::Result<Signature, CryptoMaterialError> {
        match p256::ecdsa::Signature::try_from(bytes) {
            Ok(p256_signature) => Ok(Signature(p256_signature)),
            Err(_) => Err(CryptoMaterialError::DeserializationError),
        }
    }
```

**File:** crates/aptos-crypto/src/secp256r1_ecdsa/secp256r1_ecdsa_sigs.rs (L64-85)
```rust
    pub fn check_s_malleability(bytes: &[u8]) -> std::result::Result<(), CryptoMaterialError> {
        if bytes.len() != SIGNATURE_LENGTH {
            return Err(CryptoMaterialError::WrongLengthError);
        }
        if !Signature::check_s_lt_order_half(&bytes[32..]) {
            return Err(CryptoMaterialError::CanonicalRepresentationError);
        }
        Ok(())
    }

    /// Check if S < ORDER_HALF to capture invalid signatures.
    fn check_s_lt_order_half(s: &[u8]) -> bool {
        for i in 0..32 {
            match s[i].cmp(&ORDER_HALF[i]) {
                Ordering::Less => return true,
                Ordering::Greater => return false,
                _ => {},
            }
        }
        // At this stage S == ORDER_HALF which implies a non canonical S.
        false
    }
```

**File:** crates/aptos-crypto/src/secp256r1_ecdsa/secp256r1_ecdsa_sigs.rs (L133-141)
```rust









```

**File:** crates/aptos-crypto/src/secp256r1_ecdsa/secp256r1_ecdsa_sigs.rs (L169-176)
```rust








```

**File:** types/src/transaction/webauthn.rs (L153-164)
```rust
        // Note: We must call verify_arbitrary_msg instead of verify here. We do NOT want to
        // use verify because it BCS serializes and prefixes the message with a hash
        // via the signing_message function invocation
        match (&public_key, &self.signature) {
            (
                AnyPublicKey::Secp256r1Ecdsa { public_key },
                AssertionSignature::Secp256r1Ecdsa { signature },
            ) => signature.verify_arbitrary_msg(&verification_data, public_key),
            _ => Err(anyhow!(
                "WebAuthn verification failure, invalid key, signature pairing"
            )),
        }
```
