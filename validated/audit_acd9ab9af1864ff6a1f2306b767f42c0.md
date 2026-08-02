Let me look at the actual TryFrom implementation and validate() function for the secp256r1 PrivateKey. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/aptos-crypto/src/secp256r1_ecdsa/secp256r1_ecdsa_keys.rs (L1-21)
```rust
// Copyright (c) Aptos Foundation
// Licensed pursuant to the Innovation-Enabling Source Code License, available at https://github.com/aptos-labs/aptos-core/blob/main/LICENSE

//! This file implements traits for Secp256r1 ECDSA private keys and public keys.

#[cfg(any(test, feature = "fuzzing"))]
use crate::test_utils::{self, KeyPair};
use crate::{
    hash::CryptoHash,
    secp256r1_ecdsa::{Signature, ORDER, PRIVATE_KEY_LENGTH, PUBLIC_KEY_LENGTH},
    traits::{PrivateKey as PrivateKeyTrait, PublicKey as PublicKeyTrait, *},
};
use aptos_crypto_derive::{key_name, DeserializeKey, SerializeKey, SilentDebug, SilentDisplay};
use core::convert::TryFrom;
use num_bigint::BigUint;
use num_integer::Integer;
use p256::{self, ecdsa::signature::Signer};
#[cfg(any(test, feature = "fuzzing"))]
use proptest::prelude::*;
use serde::Serialize;
use std::fmt;
```

**File:** crates/aptos-crypto/src/secp256r1_ecdsa/secp256r1_ecdsa_keys.rs (L23-26)
```rust
/// A secp256r1_ecdsa private key
#[derive(DeserializeKey, SerializeKey, SilentDebug, SilentDisplay)]
#[key_name("Secp256r1EcdsaPrivateKey")]
pub struct PrivateKey(pub(crate) p256::ecdsa::SigningKey);
```

**File:** crates/aptos-crypto/src/secp256r1_ecdsa/secp256r1_ecdsa_sigs.rs (L9-18)
```rust
    secp256r1_ecdsa::{PrivateKey, PublicKey, ORDER_HALF},
    traits::{Signature as SignatureTrait, *},
};
use anyhow::{anyhow, Result};
use aptos_crypto_derive::{key_name, DeserializeKey, SerializeKey};
use core::convert::TryFrom;
use p256::NonZeroScalar;
use serde::Serialize;
use signature::Verifier;
use std::{cmp::Ordering, fmt};
```
