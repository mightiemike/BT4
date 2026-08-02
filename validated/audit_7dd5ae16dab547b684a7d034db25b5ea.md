No vulnerability found for this question.

**Analysis:**

The `dealt_secret_key_impl!` macro in `crates/aptos-dkg/src/pvss/dealt_secret_key.rs` binds `DEALT_SK_NUM_BYTES`, the byte-conversion function, and the group type together as a single, atomic macro instantiation. [1](#0-0) 

In production code, this macro is only ever instantiated once, for `g1`, with all three type/function parameters (`G1_PROJ_NUM_BYTES`, `g1_proj_from_bytes`, `G1Projective`) coming from the same call site — the `g2` module is empty (commented out), so there is no second, differently-parameterized instantiation anywhere in the codebase to "mismatch" against. [2](#0-1) 

The same is true for `PublicParameters` in `crates/aptos-dkg/src/pvss/encryption_dlog.rs`, where `PUBLIC_PARAMS_NUM_BYTES` and the group-element deserialization routine are derived from the identical macro-parameter set within one macro expansion. [3](#0-2) 

Because `DEALT_SK_NUM_BYTES`, `TryFrom<&[u8]>`, and the underlying group type are all fixed together at compile time by the macro's parameter list, there is no runtime code path — reachable from unprivileged transaction, package, API, view, bytecode, or proof input — that could feed a `g1`-instantiated `DealtSecretKey`/`PublicParameters` a byte length or group encoding belonging to a different curve group. The types produced by `pvss::dealt_secret_key::g1` and any hypothetical `g2` instantiation are distinct Rust types; Rust's static type system, not a runtime length check, prevents mixing them, and no such mixing occurs in the actual randomness pipeline (`WVUF = weighted_vuf::pinkas::PinkasWUF` in `types/src/randomness.rs`), which consistently uses `g1`/`g2`-consistent types end-to-end for `PerBlockRandomness.seed` derivation. [4](#0-3) [5](#0-4) 

Since the premise of the question (a mismatch between the macro-bound constant and the actual group encoding causing a truncated/padded seed) requires a source-level miswiring that does not exist in the current codebase, and is not something an unprivileged transaction, API call, or proof input can trigger at runtime, this does not meet the review's decision standard for accepting a finding.

### Citations

**File:** crates/aptos-dkg/src/pvss/dealt_secret_key.rs (L4-32)
```rust
macro_rules! dealt_secret_key_impl {
    (
        $GT_PROJ_NUM_BYTES:ident,
        $gt_proj_from_bytes:ident,
        $gt_multi_exp:ident,
        $GTProjective:ident,
        $gt:ident
    ) => {
        use aptos_crypto::blstrs::{$GT_PROJ_NUM_BYTES, $gt_proj_from_bytes};
        use crate::{
            algebra::lagrange::lagrange_coefficients,
            pvss::{
                dealt_secret_key_share::$gt::DealtSecretKeyShare,
                threshold_config::ThresholdConfigBlstrs,
            },
            utils::{$gt_multi_exp},
        };
        use aptos_crypto::CryptoMaterialError;
        use aptos_crypto_derive::{SilentDebug, SilentDisplay};
        use blstrs::{$GTProjective, Scalar};
        use ff::Field;
        use more_asserts::{assert_ge, assert_le};
        use aptos_crypto::traits::{TSecretSharingConfig as _};
        use aptos_crypto::traits::{ThresholdConfig as _};
        use aptos_crypto::arkworks::shamir::Reconstructable;
        use aptos_crypto::arkworks::shamir::ShamirShare;

        /// The size of a serialized *dealt secret key*.
        pub(crate) const DEALT_SK_NUM_BYTES: usize = $GT_PROJ_NUM_BYTES;
```

**File:** crates/aptos-dkg/src/pvss/dealt_secret_key.rs (L127-146)
```rust
pub mod g1 {
    dealt_secret_key_impl!(
        G1_PROJ_NUM_BYTES,
        g1_proj_from_bytes,
        g1_multi_exp,
        G1Projective,
        g1
    );
}

pub mod g2 {
    // dealt_secret_key_impl!(
    //     G2_PROJ_NUM_BYTES,
    //     g2_proj_from_bytes,
    //     g2_multi_exp,
    //     G2Projective,
    //     g2
    // );
}

```

**File:** crates/aptos-dkg/src/pvss/encryption_dlog.rs (L5-35)
```rust
macro_rules! encryption_dlog_pp_impl {
    ($GT_PROJ_NUM_BYTES:ident, $gt_proj_from_bytes:ident, $GTProjective:ident) => {
        pub const PUBLIC_PARAMS_NUM_BYTES: usize = $GT_PROJ_NUM_BYTES;

        /// The public parameters used in the encryption scheme.
        #[derive(DeserializeKey, Clone, SerializeKey, PartialEq, Debug, Eq)]
        pub struct PublicParameters {
            /// A group element $h \in G$, where $G$ is $G_1$, $G_2$ or $G_T$.
            h: $GTProjective,
        }

        impl PublicParameters {
            pub fn new(h: $GTProjective) -> Self {
                Self { h }
            }

            pub fn to_bytes(&self) -> [u8; $GT_PROJ_NUM_BYTES] {
                self.h.to_compressed()
            }

            pub fn as_group_element(&self) -> &$GTProjective {
                &self.h
            }
        }

        impl TryFrom<&[u8]> for PublicParameters {
            type Error = CryptoMaterialError;

            fn try_from(bytes: &[u8]) -> std::result::Result<PublicParameters, Self::Error> {
                $gt_proj_from_bytes(bytes).map(|h| PublicParameters { h })
            }
```

**File:** types/src/randomness.rs (L11-21)
```rust
pub type WVUF = weighted_vuf::pinkas::PinkasWUF;
pub type WvufPP = <WVUF as WeightedVUF>::PublicParameters;
pub type PK = <WVUF as WeightedVUF>::PubKey;
pub type SKShare = <WVUF as WeightedVUF>::SecretKeyShare;
pub type PKShare = <WVUF as WeightedVUF>::PubKeyShare;
pub type ASK = <WVUF as WeightedVUF>::AugmentedSecretKeyShare;
pub type APK = <WVUF as WeightedVUF>::AugmentedPubKeyShare;
pub type ProofShare = <WVUF as WeightedVUF>::ProofShare;
pub type Delta = <WVUF as WeightedVUF>::Delta;
pub type Evaluation = <WVUF as WeightedVUF>::Evaluation;
pub type Proof = <WVUF as WeightedVUF>::Proof;
```

**File:** testsuite/smoke-test/src/randomness/mod.rs (L243-256)
```rust
    let rand_metadata = RandMetadata {
        epoch: on_chain_block_randomness.epoch,
        round: on_chain_block_randomness.round,
    };
    let input = bcs::to_bytes(&rand_metadata).unwrap();
    let output = WVUF::eval(&dealt_secret, input.as_slice());
    let output_serialized = bcs::to_bytes(&output).unwrap();
    let expected_randomness_seed = Sha3_256::digest(output_serialized.as_slice()).to_vec();

    ensure!(
        expected_randomness_seed == on_chain_block_randomness.seed.clone().unwrap(),
        "randomness verification failed with final check failure"
    );
    Ok(())
```
