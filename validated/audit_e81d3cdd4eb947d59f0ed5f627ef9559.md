### Title
`op_bls_verify` Accepts Empty (pubkey, message) Pair List, Enabling Trivial Signature Verification Bypass — (File: `src/bls_ops.rs`)

---

### Summary

`op_bls_verify` in `src/bls_ops.rs` does not validate that at least one `(pubkey, message)` pair is provided after the signature argument. When called with only a G2 signature and no pairs, `aggregate_verify` is invoked with an empty iterator. By BLS aggregate verification semantics, an empty pair list causes the pairing check to reduce to `e(sig, −g₂) = 1`, which holds if and only if `sig` is the identity G2 element — a fixed, publicly known constant. An attacker who controls the arguments to `bls_verify` can always satisfy this check without possessing any private key.

---

### Finding Description

In `op_bls_verify` (`src/bls_ops.rs`, lines 364–400), the function extracts the G2 signature from the first argument, then iterates over the remaining arguments to collect `(G1 pubkey, message)` pairs into `items`: [1](#0-0) 

After the loop, if no pairs were provided, `items` is an empty `Vec`. The function then calls: [2](#0-1) 

There is **no guard** of the form `if items.is_empty() { return Err(...) }` before this call.

By BLS12-381 aggregate verification math, with an empty set of `(pubkey, message)` pairs the aggregate public key is the group identity. The pairing equation collapses to:

```
e(sig, −g₂) = 1   ⟺   sig = identity G2 element
```

The identity G2 element in compressed form is the fixed 96-byte constant `0xc0` followed by 95 zero bytes. An attacker can always supply this value. The `chia_bls` `aggregate_verify` call with an empty iterator will return `true` for this input, causing `op_bls_verify` to return `Ok(Reduction(cost, a.nil()))` — a successful verification — without any real signature being checked against any real public key.

The analogous missing check in the external report was `listing.tokenIds > 0` in `_createListing()`. Here the missing check is `items.is_empty()` in `op_bls_verify`.

---

### Impact Explanation

Any CLVM puzzle that uses `bls_verify` in a design where the solution supplies the `(pubkey, message)` pairs — for example, flexible multi-signature schemes, threshold-signature puzzles, or delegation patterns — can be bypassed. The attacker provides the identity G2 element as the signature and omits all `(pubkey, message)` pairs. The operator returns success, the puzzle's authentication condition is satisfied, and the coin can be spent without the attacker holding any private key.

The corrupted result is the `Response` value: `Ok(Reduction(BLS_PAIRING_BASE_COST, nil))` is returned instead of `Err(EvalErr::BLSVerifyFailed(...))`.

---

### Likelihood Explanation

**Medium.** The vulnerability requires a puzzle design in which the solution provides the `(pubkey, message)` pairs rather than having them hardcoded in the puzzle body. Puzzles that hardcode both the public key and the message are not affected. However, flexible multi-sig and delegation patterns — where the solution specifies which keys and messages to verify — are a realistic and documented use case for `bls_verify`, making this exploitable in practice for such designs.

The attacker-controlled entry path is direct: craft CLVM program bytes that invoke opcode `59` (`bls_verify`) with a single 96-byte atom equal to the compressed identity G2 point and no further arguments. This is reachable through any interface that accepts and runs attacker-supplied CLVM programs, including the mempool.

---

### Recommendation

Add an emptiness guard immediately after the `while` loop in `op_bls_verify`:

```rust
if items.is_empty() {
    return Err(EvalErr::InvalidOpArg(
        input,
        "bls_verify requires at least one (pubkey, message) pair".to_string(),
    ));
}
```

This mirrors the fix described in the external report (`listing.tokenIds > 0`) and ensures that a call with no pairs is rejected rather than trivially succeeding.

---

### Proof of Concept

Construct a CLVM argument list for opcode `59` (`bls_verify`) containing exactly one atom: the 96-byte compressed identity G2 element.

```
; identity G2 = 0xc000...00 (96 bytes, first byte 0xc0, rest 0x00)
(bls_verify <0xc000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000>)
```

- `first(a, args)` succeeds and extracts the identity G2 element as the signature. [3](#0-2) 
- `rest(a, args)` yields nil; the `while !nilp(a, args)` loop body never executes. [4](#0-3) 
- `items` is empty; `aggregate_verify(&identity_g2, [])` returns `true`. [5](#0-4) 
- `op_bls_verify` returns `Ok(Reduction(BLS_PAIRING_BASE_COST, nil))` — verification succeeds with no pubkey or message checked.

### Citations

**File:** src/bls_ops.rs (L373-394)
```rust
    let mut args = input;

    // the first argument is the signature
    let signature = a.g2(first(a, args)?)?;

    // followed by a variable number of (G1, msg)-pairs (as a flat list)
    args = rest(a, args)?;

    let mut items = Vec::<(PublicKey, Atom)>::new();
    while !nilp(a, args) {
        let pk = a.g1(first(a, args)?)?;
        args = rest(a, args)?;
        let msg = atom(a, first(a, args)?, "bls_verify message")?;
        args = rest(a, args)?;

        cost += BLS_PAIRING_COST_PER_ARG;
        cost += msg.as_ref().len() as Cost * BLS_MAP_TO_G2_COST_PER_BYTE;
        cost += DST_G2.len() as Cost * BLS_MAP_TO_G2_COST_PER_DST_BYTE;
        check_cost(cost, max_cost)?;

        items.push((pk, msg));
    }
```

**File:** src/bls_ops.rs (L396-400)
```rust
    if !aggregate_verify(&signature, items) {
        Err(EvalErr::BLSVerifyFailed(input))?
    } else {
        Ok(Reduction(cost, a.nil()))
    }
```
