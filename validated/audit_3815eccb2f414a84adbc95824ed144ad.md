No vulnerability found for this question.

**Reasoning:** The premise that `unsafe_hash_to_affine` processes unprivileged/attacker-controlled DKG transcript bytes during transcript verification does not hold. Tracing all call sites of `unsafe_hash_to_affine`:

1. In [1](#0-0)  — this is a deterministic, hardcoded input computed once at parameter-construction time, not attacker-supplied transcript data.

2. In [2](#0-1) , again not attacker-controlled or transcript-verification-related input.

There is no code path where `unsafe_hash_to_affine` is invoked on an arbitrary/unprivileged message during DKG transcript verification. The function itself is deterministic and well-defined given identical inputs [3](#0-2) , and its own test suite explicitly asserts determinism [4](#0-3) . Any divergence would require differing `arkworks`/SHA3 dependency versions producing different results for identical byte inputs — a dependency-only claim without a repo root cause, which is explicitly out of scope per the review rules. Since no unprivileged transcript byte-string ever reaches this function, the described exploit path does not exist in this codebase.

### Citations

**File:** crates/aptos-dkg/src/pvss/chunky/chunked_elgamal_pp.rs (L75-82)
```rust
    fn default_parameters() -> (C::Affine, C::Affine) {
        let G = hashing::unsafe_hash_to_affine(b"G", DST);
        // Chunky's encryption pubkey base must match up with the blst base, since validators
        // reuse their consensus keypairs as encryption keypairs
        let H = C::Affine::generator();
        debug_assert_ne!(G, H);
        (G, H)
    }
```

**File:** crates/aptos-crypto/src/arkworks/random.rs (L46-58)
```rust
/// Faster "unsafe" random point by hashing some random bytes to the curve
/// But still not very fast
pub fn unsafe_random_point<A: AffineRepr, R>(rng: &mut R) -> A
where
    R: rand_core::RngCore + rand_core::CryptoRng,
{
    // Generate some random bytes
    let mut buf = [0u8; 32];
    rng.fill_bytes(&mut buf);

    // Hash to curve (using unsafe_hash_to_affine)
    hashing::unsafe_hash_to_affine(&buf, b"unsafe_random_point")
}
```

**File:** crates/aptos-crypto/src/arkworks/hashing.rs (L27-51)
```rust
pub fn unsafe_hash_to_affine<P: AffineRepr>(msg: &[u8], dst: &[u8]) -> P {
    let dst_len = u8::try_from(dst.len())
        .expect("DST is too long; its length must be <= 255, as in RFC 9380 (Section 5.3.1)");

    let mut buf = Vec::with_capacity(msg.len() + dst.len() + 1);
    buf.extend_from_slice(msg);
    buf.extend_from_slice(dst);
    buf.push(dst_len);
    buf.push(0); // placeholder for counter

    for ctr in 0..=u8::MAX {
        *buf.last_mut()
            .expect("Could not access last byte of buffer") = ctr;

        let hashed = sha3::Sha3_512::digest(&buf);

        // `from_random_bytes()` first tries to construct an x-coordinate, and then a y-coordinate from that, see e.g.:
        // https://github.com/arkworks-rs/algebra/blob/c1f4f5665504154a9de2345f464b0b3da72c28ec/ec/src/models/short_weierstrass/affine.rs#L264
        if let Some(p) = P::from_random_bytes(&hashed) {
            return p.mul_by_cofactor(); // is needed to ensure that `p` lies in the prime order subgroup
        }
    }

    panic!("Failed to hash to affine group element");
}
```

**File:** crates/aptos-crypto/src/arkworks/hashing.rs (L76-84)
```rust
    fn test_determinism<P: AffineRepr>() {
        let msg = b"hello world";
        let dst = b"my-domain-separator";

        let p1: P = unsafe_hash_to_affine(msg, dst);
        let p2: P = unsafe_hash_to_affine(msg, dst);

        assert_eq!(p1, p2);
    }
```
