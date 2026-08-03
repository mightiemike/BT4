[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/framework/aptos-stdlib/sources/cryptography/single_key.move (L83-85)
```text
        } else if (scheme_id == WEB_AUTHN_PUBLIC_KEY_TYPE) {
            let public_key_bytes = bcs_stream::deserialize_vector(stream, |x| deserialize_u8(x));
            pk = AnyPublicKey::Secp256r1Ecdsa{pk: secp256r1::ecdsa_raw_public_key_from_64_bytes(public_key_bytes)};
```

**File:** aptos-move/framework/aptos-stdlib/sources/cryptography/single_key.move (L116-120)
```text
    public fun to_authentication_key(self: &AnyPublicKey): vector<u8> {
        let pk_bytes = bcs::to_bytes(self);
        pk_bytes.push_back(SIGNATURE_SCHEME_ID);
        hash::sha3_256(pk_bytes)
    }
```

**File:** api/types/src/transaction.rs (L1975-1977)
```rust
            PublicKey::Secp256r1Ecdsa(p) => {
                AnyPublicKey::secp256r1_ecdsa(p.value.inner().try_into()?)
            },
```

**File:** types/src/transaction/authenticator.rs (L1510-1512)
```rust
    pub fn to_bytes(&self) -> Vec<u8> {
        bcs::to_bytes(self).expect("Only unhandleable errors happen here.")
    }
```
