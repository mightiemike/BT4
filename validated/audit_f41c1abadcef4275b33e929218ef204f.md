[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/aptos-batch-encryption/src/shared/encryption_key.rs (L12-21)
```rust
use aptos_crypto::arkworks::serialization::{ark_de, ark_se};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct EncryptionKey {
    #[serde(serialize_with = "ark_se", deserialize_with = "ark_de")]
    pub(crate) sig_mpk_g2: G2Affine,
    #[serde(serialize_with = "ark_se", deserialize_with = "ark_de")]
    pub(crate) tau_g2: G2Affine,
}
```

**File:** crates/aptos-dkg/src/pvss/chunky/subtranscript.rs (L43-59)
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
```

**File:** types/src/keyless/groth16_vk.rs (L70-90)
```rust
        // NOTE: Technically, we already validate the points when we set the VK in Move, so we could
        // make this 2x faster by avoiding the point validation checks  via
        // `deserialize_with_mode(..., Compress::Yes, Validate::No)`. Due to paranoia, will not
        // optimize this for now.
        Ok(Self::from(VerifyingKey {
            alpha_g1: G1Affine::deserialize_compressed(vk.alpha_g1.as_slice())
                .map_err(|_| CryptoMaterialError::DeserializationError)?,
            beta_g2: G2Affine::deserialize_compressed(vk.beta_g2.as_slice())
                .map_err(|_| CryptoMaterialError::DeserializationError)?,
            gamma_g2: G2Affine::deserialize_compressed(vk.gamma_g2.as_slice())
                .map_err(|_| CryptoMaterialError::DeserializationError)?,
            delta_g2: G2Affine::deserialize_compressed(vk.delta_g2.as_slice())
                .map_err(|_| CryptoMaterialError::DeserializationError)?,
            gamma_abc_g1: vec![
                G1Affine::deserialize_compressed(vk.gamma_abc_g1[0].as_slice())
                    .map_err(|_| CryptoMaterialError::DeserializationError)?,
                G1Affine::deserialize_compressed(vk.gamma_abc_g1[1].as_slice())
                    .map_err(|_| CryptoMaterialError::DeserializationError)?,
            ],
        }))
    }
```
