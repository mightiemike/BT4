[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/aptos-dkg/src/pvss/chunky/chunked_elgamal.rs (L5-11)
```rust
use crate::{
    dlog::bsgs,
    pvss::chunky::chunks,
    sigma_protocol,
    sigma_protocol::homomorphism::{self, fixed_base_msms, EntrywiseMap},
    Scalar,
};
```

**File:** crates/aptos-dkg/src/sigma_protocol/homomorphism/tuple.rs (L205-215)
```rust
    fn try_map<U, E, F>(self, mut f: F) -> Result<Self::Output<U>, E>
    where
        F: FnMut(T) -> Result<U, E>,
        U: CanonicalSerialize + CanonicalDeserialize + Clone + Debug + Eq,
    {
        Ok(TupleCodomainShape(
            self.0.try_map(&mut f)?,
            self.1.try_map(f)?,
        ))
    }
}
```

**File:** crates/aptos-dkg/src/pvss/chunky/chunked_scalar_mul.rs (L99-107)
```rust
    fn try_map<U, E, F>(self, f: F) -> Result<Self::Output<U>, E>
    where
        F: FnMut(T) -> Result<U, E>,
        U: CanonicalSerialize + CanonicalDeserialize + Clone + Debug + Eq,
    {
        Ok(CodomainShape(
            self.0.into_iter().map(f).collect::<Result<Vec<_>, _>>()?,
        ))
    }
```

**File:** crates/aptos-dkg/src/pvss/chunky/weighted_transcript_v2.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```
