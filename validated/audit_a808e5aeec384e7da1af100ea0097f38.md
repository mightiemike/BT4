### Title
Flag-Gated Secp Operator Bypass via 4-Byte Opcode Alias — (`File: src/chia_dialect.rs`)

---

### Summary

The `ENABLE_SECP_OPS` flag is intended to gate `secp256k1_verify` and `secp256r1_verify` as a soft-fork activation control. However, the `ChiaDialect::op()` dispatch function contains a second, ungated path to the same operator implementations via their 4-byte opcode aliases (`0x13d61f00` and `0x1c3a8f00`). An attacker-controlled CLVM program can invoke real secp signature verification without the flag being set, bypassing the intended pre-fork/post-fork boundary.

---

### Finding Description

`ChiaDialect::op()` in `src/chia_dialect.rs` has two separate dispatch branches:

**Branch 1 — 4-byte opcodes (lines 157–183), ungated:**
```rust
if op_len == 4 {
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,   // no flag check
        0x1c3a8f00 => op_secp256r1_verify,   // no flag check
        _ => { return unknown_operator(...); }
    };
    return f(allocator, argument_list, max_cost, flags);
}
```

**Branch 2 — 1-byte opcodes (lines 248–249), flag-gated:**
```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The flag `ENABLE_SECP_OPS` (value `0x0800`) is documented as: *"Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify)."* It is absent from `MEMPOOL_MODE` and from `ClvmFlags::empty()`. The 4-byte opcodes `0x13d61f00` and `0x1c3a8f00` were chosen specifically to encode the secp costs (1,300,000 and 1,850,000 respectively) in the unknown-op cost formula, but they dispatch to the real implementations unconditionally. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

**Broken invariant:** A caller that constructs `ChiaDialect::new(ClvmFlags::empty())` (no `ENABLE_SECP_OPS`) expects secp verification to be unavailable — either rejected (mempool mode with `NO_UNKNOWN_OPS`) or treated as a cost-only no-op (consensus mode). Instead, the 4-byte path executes `op_secp256k1_verify` / `op_secp256r1_verify` from `src/secp_ops.rs` in full, performing real ECDSA verification and returning nil on success or raising on failure.

**Consensus divergence:** A node running without `ENABLE_SECP_OPS` (pre-fork state) should behave identically to a node that has no secp code at all. A node without secp code treats `0x13d61f00` as an unknown operator and returns nil with cost 1,300,000 regardless of arguments. A node with this code but without `ENABLE_SECP_OPS` instead performs real signature verification and raises on a bad signature. These two nodes disagree on the validity of any spend bundle whose puzzle uses the 4-byte secp opcode with an invalid signature — a direct consensus split. [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation

**Impact: 4 / Likelihood: 3**

The entry path is fully attacker-controlled: any CLVM program submitted to a node (puzzle bytes in a spend bundle) can embed the 4-byte opcode `0x13d61f00` or `0x1c3a8f00`. No social engineering is required. The attacker only needs to know the 4-byte opcode values, which are derivable from the cost formula documented in the source. The trigger is deterministic and reproducible. Likelihood is moderate rather than high because exploitation requires the network to be in a pre-fork state where `ENABLE_SECP_OPS` is not universally set, and the practical window depends on deployment timing.

---

### Recommendation

Add the `ENABLE_SECP_OPS` flag check to the 4-byte dispatch branch, mirroring the 1-byte branch:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => { return unknown_operator(allocator, o, argument_list, flags, max_cost); }
};
```

This ensures both opcode encodings are subject to the same activation gate, eliminating the bypass path and restoring consistent pre-fork/post-fork behavior across all nodes. [6](#0-5) 

---

### Proof of Concept

Construct a CLVM program using the 4-byte opcode directly. With `flags = ClvmFlags::empty()` (no `ENABLE_SECP_OPS`):

```
; opcode bytes: 0x13 0xd6 0x1f 0x00  (secp256k1_verify, 4-byte form)
; program: (0x13d61f00 pubkey msg sig)
```

**Expected behavior (flag not set):** treated as unknown op → returns nil, cost 1,300,000, no signature check.

**Actual behavior:** `op_secp256k1_verify` executes; raises `Secp256Failed` if the signature is invalid, returns nil if valid — identical to behavior with `ENABLE_SECP_OPS` set.

A node without secp code at all would return nil unconditionally for this opcode. A node with this code but without the flag raises on a bad signature. The two nodes disagree: consensus split on any spend bundle using this opcode with an invalid signature. [7](#0-6) [8](#0-7)

### Citations

**File:** src/chia_dialect.rs (L62-63)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;
```

**File:** src/chia_dialect.rs (L70-76)
```rust
/// The default mode when running generators in mempool-mode (i.e. the stricter
/// mode).
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L156-183)
```rust
        let op_len = allocator.atom_len(o);
        if op_len == 4 {
            // these are unknown operators with assigned cost
            // the formula is:
            // +---+---+---+------------+
            // | multiplier|XX | XXXXXX |
            // +---+---+---+---+--------+
            //  ^           ^    ^
            //  |           |    + 6 bits ignored when computing cost
            // cost         |
            // (3 bytes)    + 2 bits
            //                cost_function

            let b = allocator.atom(o);
            let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());

            // the secp operators have a fixed cost of 1850000 and 1300000,
            // which makes the multiplier 0x1c3a8f and 0x0cf84f (there is an
            // implied +1) and cost function 0
            let f = match opcode {
                0x13d61f00 => op_secp256k1_verify,
                0x1c3a8f00 => op_secp256r1_verify,
                _ => {
                    return unknown_operator(allocator, o, argument_list, flags, max_cost);
                }
            };
            return f(allocator, argument_list, max_cost, flags);
        }
```

**File:** src/chia_dialect.rs (L248-249)
```rust
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

**File:** src/secp_ops.rs (L61-103)
```rust
pub fn op_secp256k1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let cost = SECP256K1_VERIFY_COST;
    check_cost(cost, max_cost)?;

    let [pubkey, msg, sig] = get_args::<3>(a, input, "secp256k1_verify")?;

    // first argument is sec1 encoded pubkey
    let pubkey = atom(a, pubkey, "secp256k1_verify pubkey")?;
    let verifier = K1VerifyingKey::from_sec1_bytes(pubkey.as_ref()).map_err(|_| {
        EvalErr::InvalidOpArg(input, "secp256k1_verify: pubkey is not valid".to_string())
    })?;

    // second arg is message
    let msg = atom(a, msg, "secp256k1_verify msg")?;
    if msg.as_ref().len() != 32 {
        Err(EvalErr::InvalidOpArg(
            input,
            "secp256k1_verify: message digest is not 32 bytes".to_string(),
        ))?;
    }

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
```
