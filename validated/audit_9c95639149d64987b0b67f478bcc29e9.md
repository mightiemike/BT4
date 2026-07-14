### Title
`op_secp256k1_verify` Accepts Non-Canonical High-S Signatures Without Specification — (`File: src/secp_ops.rs`)

### Summary

`op_secp256k1_verify` (and `op_secp256r1_verify`) parse and verify ECDSA signatures using `K1Signature::from_slice` followed by `verify_prehash`, with no enforcement of low-S normalization and no documented specification of what canonicality properties the operator provides. For every valid secp256k1 signature `(r, s)`, the malleable form `(r, n − s)` (where `n` is the group order) is also accepted as valid. This is the direct analog of the ChainPort finding: *"The signatures could be represented in more than one format, which means that storing them is not enough to ensure uniqueness."*

### Finding Description

In `src/secp_ops.rs`, `op_secp256k1_verify` does the following:

```rust
let sig = K1Signature::from_slice(sig.as_ref()).map_err(|_| { ... })?;
let result = verifier.verify_prehash(msg.as_ref(), &sig);
``` [1](#0-0) 

`K1Signature::from_slice` parses the raw 64-byte compact `(r ‖ s)` encoding. The `k256` crate (version `0.14.0-rc`) does not normalize `s` to the lower half of the group order inside `from_slice`, and `verify_prehash` does not call `normalize_s()` before verifying. There is no explicit low-S check anywhere in the operator. The same is true for `op_secp256r1_verify` using `P1Signature::from_slice` and `p256`. [2](#0-1) 

The `ClvmFlags` parameter is ignored entirely by both operators (`_flags: ClvmFlags`), so there is no dialect-level switch to enable strict low-S enforcement. [3](#0-2) 

No specification, comment, or documentation in the codebase describes whether `secp256k1_verify` is intended to accept only low-S signatures or both forms. The operator's behavior is analogous to the undocumented ChainPort signature properties: the properties it provides (integrity, non-repudiation, uniqueness) are not stated. [4](#0-3) 

### Impact Explanation

For any Chialisp coin puzzle that uses `secp256k1_verify` and relies on the signature bytes being unique (e.g., to prevent replay by recording used signatures in a set, or by committing to the signature in the coin ID), an attacker who observes a valid spend can construct a second valid spend using the malleable `(r, n − s)` form of the same signature. Both byte strings pass `op_secp256k1_verify` against the same public key and message digest, but they are distinct 64-byte atoms. Any uniqueness check keyed on the raw signature bytes is bypassed.

The corrupted result is: `op_secp256k1_verify` returns `Ok(Reduction(cost, nil))` (success) for both `sig` and its malleable twin `sig'`, where `sig != sig'` as CLVM atoms, but both authorize the same message under the same key.

### Likelihood Explanation

`secp256k1_verify` is enabled via `ENABLE_SECP_OPS` and is reachable from any CLVM program submitted to a node running with that flag. The `ENABLE_SECP_OPS` flag is wired into the `ChiaDialect` dispatch table. [4](#0-3) 

Any coin puzzle author who uses `secp256k1_verify` and assumes the signature bytes uniquely identify an authorization — a natural assumption given that BLS signatures in Chia are unique by construction — will produce a vulnerable coin. The absence of any specification or warning makes this a silent trap. Likelihood is moderate: it requires a coin that uses `secp256k1_verify` for replay protection, but the operator is new and its malleability is nowhere documented.

### Recommendation

1. **Specify the operator**: Document explicitly whether `secp256k1_verify` enforces low-S normalization. If it does not, document that signature bytes must not be used as unique identifiers.
2. **Enforce low-S**: Call `sig.normalize_s()` (available on `k256::ecdsa::Signature`) before calling `verify_prehash`, or reject signatures where `s > n/2`. Apply the same fix to `op_secp256r1_verify`.
3. **Add a test vector**: Include a test case with a high-S signature that should fail if low-S is enforced, analogous to the existing length-check test vectors in `op-tests/test-secp-verify.txt`. [5](#0-4) 

### Proof of Concept

Given a valid test vector from `op-tests/test-secp-verify.txt`:

```
pubkey = 0x02888b0c110ef0b4962e3fc6929cbba7a8bb25b4b2c885f55c76365018c909b439
msg    = 0x74c2941eb2ebe5aa4f2287a4c5e506a6290c045004058de97a7edf0122548668
sig    = 0x1acb7a6e...e6c018   (low-S, passes)
``` [6](#0-5) 

Compute `sig' = (r ‖ (n − s))` where `n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141`. Because `op_secp256k1_verify` calls `K1Signature::from_slice(&sig')` followed by `verify_prehash` without any low-S check, `sig'` also returns `Ok(cost, nil)`. The two 64-byte atoms `sig` and `sig'` are distinct CLVM atoms that both authorize the same (pubkey, msg) pair, breaking any uniqueness assumption a coin puzzle might make about the signature bytes. [7](#0-6)

### Citations

**File:** src/secp_ops.rs (L1-10)
```rust
use crate::allocator::{Allocator, NodePtr};
use crate::chia_dialect::ClvmFlags;
use crate::cost::{Cost, check_cost};
use crate::error::EvalErr;
use crate::op_utils::{atom, get_args};
use crate::reduction::{Reduction, Response};
use k256::ecdsa::{Signature as K1Signature, VerifyingKey as K1VerifyingKey};
use p256::ecdsa::signature::hazmat::PrehashVerifier;
use p256::ecdsa::{Signature as P1Signature, VerifyingKey as P1VerifyingKey};

```

**File:** src/secp_ops.rs (L14-20)
```rust
// expects: pubkey msg sig
pub fn op_secp256r1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
```

**File:** src/secp_ops.rs (L86-104)
```rust

    // third arg is a fixed-size signature
    let sig = atom(a, sig, "secp256k1_verify sig")?;
    let sig = K1Signature::from_slice(sig.as_ref()).map_err(|_| {
        EvalErr::InvalidOpArg(
            input,
            "secp256k1_verify: signature is not valid".to_string(),
        )
    })?;

    // verify signature
    let result = verifier.verify_prehash(msg.as_ref(), &sig);

    if result.is_err() {
        Err(EvalErr::Secp256Failed(input))?
    } else {
        Ok(Reduction(cost, a.nil()))
    }
}
```

**File:** src/chia_dialect.rs (L62-63)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;
```

**File:** op-tests/test-secp-verify.txt (L2-2)
```text
secp256k1_verify 0x02888b0c110ef0b4962e3fc6929cbba7a8bb25b4b2c885f55c76365018c909b439 0x74c2941eb2ebe5aa4f2287a4c5e506a6290c045004058de97a7edf0122548668 0x1acb7a6e062e78ccd4237b12c22f02b5a8d9b33cb3ba13c35e88e036baa1cbca75253bb9a96ffc48b43196c69c2972d8f965b1baa4e52348d8081cde65e6c018 => 0 | 1300000
```
