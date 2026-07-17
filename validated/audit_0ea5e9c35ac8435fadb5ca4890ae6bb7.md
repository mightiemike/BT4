### Title
Rosetta Construction API Silently Advertises `secp256k1` Curve Support But Fails at `/construction/payloads` for SECP256K1 Signers — (`chain/rosetta-rpc/src/models.rs`)

### Summary

The Rosetta RPC layer exposes `CurveType::Secp256k1` as a parseable, accepted curve type in its public key model, but `SignatureType` has no corresponding `Secp256k1` variant. Any call to `/construction/payloads` with a SECP256K1 signer key hits a hard error at the `SignatureType::try_from(KeyType::SECP256K1)` conversion, making the entire Construction API flow impossible for SECP256K1 access keys — even though the API surface advertises them as valid.

### Finding Description

`CurveType` in `chain/rosetta-rpc/src/models.rs` has three variants: `Edwards25519`, `Secp256k1`, and `MlDsa65`. [1](#0-0) 

The `TryFrom<&PublicKey> for near_crypto::PublicKey` conversion accepts all three, including `CurveType::Secp256k1`, and successfully constructs a `near_crypto::PublicKey::SECP256K1`: [2](#0-1) 

However, `SignatureType` only has `Ed25519` and `MlDsa65` variants. The `TryFrom<near_crypto::KeyType> for SignatureType` conversion explicitly returns an error for `SECP256K1`: [3](#0-2) 

In `construction_payloads`, line 777 calls `signer_public_access_key.key_type().try_into()?` to populate the `signature_type` field of the signing payload. For a SECP256K1 key, this `?` propagates the `InvalidInput("SECP256K1 keys are not supported in Rosetta")` error, aborting the entire endpoint: [4](#0-3) 

The asymmetry is:
- `CurveType::Secp256k1` → accepted in `TryFrom<&PublicKey>` → valid `near_crypto::PublicKey::SECP256K1` constructed
- `KeyType::SECP256K1` → rejected in `TryFrom<near_crypto::KeyType> for SignatureType` → `construction_payloads` fails

### Impact Explanation

Any Rosetta client holding a SECP256K1 access key (a valid NEAR key type, used by ETH-implicit accounts and any account that has added a SECP256K1 key) can successfully call `/construction/metadata` (which only queries the access key nonce), but will receive a hard error at `/construction/payloads`. The Construction API flow is permanently broken for this key type. Since `CurveType::Secp256k1` is part of the schema and accepted in the public key parsing path, Rosetta clients have no way to know in advance that the Construction API will fail — the capability is advertised but not delivered.

### Likelihood Explanation

SECP256K1 keys are a first-class NEAR key type. ETH-implicit accounts are created with SECP256K1 keys, and any named account can add a SECP256K1 access key. Any Rosetta integrator (exchange, wallet, bridge) that attempts to use the Construction API with such a key will hit this failure. The trigger is entirely unprivileged and user-controlled: simply supplying `curve_type: "secp256k1"` in the `/construction/payloads` request body.

### Recommendation

Either:
1. Add `Secp256k1` / `EcdsaRecovery` to `SignatureType` and wire it through `From<SignatureType> for near_crypto::KeyType`, so the Construction API can complete for SECP256K1 signers; or
2. Reject `CurveType::Secp256k1` early in `TryFrom<&PublicKey> for near_crypto::PublicKey` with a clear error, and remove `Secp256k1` from `CurveType` so the API surface truthfully reflects what is supported.

The current state — accepting `Secp256k1` in the public key model but rejecting it deep inside `construction_payloads` — is the exact capability-truthfulness mismatch that causes silent failures for downstream integrators.

### Proof of Concept

```
# 1. Account "alice.near" has a SECP256K1 access key.
# 2. Call /construction/metadata — succeeds (nonce lookup works fine).
POST /construction/metadata
{ "public_keys": [{ "hex_bytes": "<secp256k1-pubkey-hex>", "curve_type": "secp256k1" }], ... }
→ 200 OK, returns nonce

# 3. Call /construction/payloads with the same key — fails.
POST /construction/payloads
{ "public_keys": [{ "hex_bytes": "<secp256k1-pubkey-hex>", "curve_type": "secp256k1" }], ... }
→ 500 { "message": "SECP256K1 keys are not supported in Rosetta" }
```

The failure is triggered at `chain/rosetta-rpc/src/lib.rs:777` via `SignatureType::try_from(KeyType::SECP256K1)` returning `Err`. [5](#0-4)

### Citations

**File:** chain/rosetta-rpc/src/models.rs (L1251-1267)
```rust
impl TryFrom<&PublicKey> for near_crypto::PublicKey {
    type Error = near_crypto::ParseKeyError;

    fn try_from(PublicKey { curve_type, hex_bytes }: &PublicKey) -> Result<Self, Self::Error> {
        Ok(match curve_type {
            CurveType::Edwards25519 => {
                near_crypto::PublicKey::ED25519((hex_bytes.as_ref() as &[u8]).try_into()?)
            }
            CurveType::Secp256k1 => {
                near_crypto::PublicKey::SECP256K1((hex_bytes.as_ref() as &[u8]).try_into()?)
            }
            CurveType::MlDsa65 => {
                near_crypto::PublicKey::MLDSA65((hex_bytes.as_ref() as &[u8]).try_into()?)
            }
        })
    }
}
```

**File:** chain/rosetta-rpc/src/models.rs (L1270-1284)
```rust
#[derive(Debug, Clone, Eq, PartialEq, serde::Serialize, serde::Deserialize, ToSchema)]
#[serde(rename_all = "lowercase")]
pub(crate) enum CurveType {
    /// `y (255-bits) || x-sign-bit (1-bit)` - 32 bytes (<https://ed25519.cr.yp.to/ed25519-20110926.pdf>)
    Edwards25519,
    /// SEC compressed - 33 bytes (<https://secg.org/sec1-v2.pdf#subsubsection.2.3.3>)
    Secp256k1,
    /// FIPS 204 ML-DSA-65 post-quantum public key - 1952 bytes
    /// (<https://csrc.nist.gov/pubs/fips/204/final>). Added upstream by
    /// <https://github.com/coinbase/mesh-specifications/pull/129>. Existing
    /// Mesh clients that haven't picked up that change yet will likely
    /// crash when they see this value; that's accepted for now.
    #[serde(rename = "ml_dsa_65")]
    MlDsa65,
}
```

**File:** chain/rosetta-rpc/src/models.rs (L1360-1372)
```rust
impl TryFrom<near_crypto::KeyType> for SignatureType {
    type Error = crate::errors::ErrorKind;

    fn try_from(key_type: near_crypto::KeyType) -> Result<Self, Self::Error> {
        match key_type {
            near_crypto::KeyType::ED25519 => Ok(Self::Ed25519),
            near_crypto::KeyType::SECP256K1 => Err(crate::errors::ErrorKind::InvalidInput(
                "SECP256K1 keys are not supported in Rosetta".to_string(),
            )),
            near_crypto::KeyType::MLDSA65 => Ok(Self::MlDsa65),
        }
    }
}
```

**File:** chain/rosetta-rpc/src/lib.rs (L773-780)
```rust
    Ok(Json(models::ConstructionPayloadsResponse {
        unsigned_transaction: unsigned_transaction.into(),
        payloads: vec![models::SigningPayload {
            account_identifier: signer_account_id.into(),
            signature_type: Some(signer_public_access_key.key_type().try_into()?),
            hex_bytes: transaction_hash.as_ref().to_vec().into(),
        }],
    }))
```
