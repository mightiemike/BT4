### Title
`ENABLE_SECP_OPS` Flag Bypassed by 4-Byte Secp Operator Encodings — (File: `src/chia_dialect.rs`)

---

### Summary

In `ChiaDialect::op()`, the 4-byte secp operator opcodes (`0x13d61f00` → `op_secp256k1_verify`, `0x1c3a8f00` → `op_secp256r1_verify`) are dispatched unconditionally at the `op_len == 4` branch, before the `ENABLE_SECP_OPS` flag check that gates the 1-byte forms (opcodes 64 and 65). The `ENABLE_SECP_OPS` flag therefore has no effect on the 4-byte encodings: secp signature verification executes regardless of whether the flag is set, including in `MEMPOOL_MODE` which deliberately omits `ENABLE_SECP_OPS`.

---

### Finding Description

`ChiaDialect::op()` dispatches operators in two distinct branches based on atom length:

**Branch 1 — 4-byte operators (lines 157–183):**

```rust
if op_len == 4 {
    let b = allocator.atom(o);
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,
        0x1c3a8f00 => op_secp256r1_verify,
        _ => { return unknown_operator(...); }
    };
    return f(allocator, argument_list, max_cost, flags); // ← no ENABLE_SECP_OPS check
}
```

**Branch 2 — 1-byte operators (lines 248–249):**

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The 4-byte branch is evaluated first and returns early. The `ENABLE_SECP_OPS` guard in Branch 2 is never reached for 4-byte-encoded secp operators. The flag description states it "Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify)" — but the 4-byte aliases for the same functions are unconditionally live.

`MEMPOOL_MODE` is defined as:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

`ENABLE_SECP_OPS` is absent. In mempool mode, 1-byte opcodes 64/65 fall through to `unknown_operator`, which with `NO_UNKNOWN_OPS` returns `EvalErr::Unimplemented`. The 4-byte forms `0x13d61f00`/`0x1c3a8f00` are dispatched directly and succeed — the flag intended to gate them is never consulted. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

Any caller that relies on the absence of `ENABLE_SECP_OPS` to prevent secp signature verification — including the standard `MEMPOOL_MODE` configuration — is bypassed by attacker-controlled CLVM bytes that use the 4-byte opcode encoding. Concretely:

- **Mempool mode, 1-byte opcode 64/65**: rejected (`EvalErr::Unimplemented` via `NO_UNKNOWN_OPS`).
- **Mempool mode, 4-byte opcode `0x13d61f00`/`0x1c3a8f00`**: `op_secp256k1_verify` / `op_secp256r1_verify` executes unconditionally.

This is a flag-wiring error: the `ENABLE_SECP_OPS` flag is structurally incapable of disabling the 4-byte secp operator forms because the 4-byte dispatch branch returns before the flag is ever checked. The result is an operator availability asymmetry between the two encodings of the same operation, and the flag's stated semantics are violated. [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation

The entry path is fully attacker-controlled: any CLVM program byte sequence submitted to `run_program` with a 4-byte atom operator `[0x13, 0xd6, 0x1f, 0x00]` or `[0x1c, 0x3a, 0x8f, 0x00]` triggers the unconditional dispatch. No special privileges, configuration, or social engineering are required. The 4-byte encoding is documented in the source comments and is reachable from the Python wheel API (`wheel/src/api.rs`) and any Rust caller of `run_program`. [6](#0-5) 

---

### Recommendation

Apply the `ENABLE_SECP_OPS` guard to the 4-byte secp operator dispatch, mirroring the 1-byte guard:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => { return unknown_operator(allocator, o, argument_list, flags, max_cost); }
};
```

This ensures the flag uniformly controls secp operator availability regardless of opcode encoding length, consistent with the flag's documented semantics. [5](#0-4) 

---

### Proof of Concept

Construct a CLVM program whose operator atom is the 4-byte sequence `[0x13, 0xd6, 0x1f, 0x00]` (secp256k1_verify) with valid `pubkey`, `msg`, `sig` arguments. Run it under `ChiaDialect::new(MEMPOOL_MODE)` — which does not include `ENABLE_SECP_OPS`. The call reaches `op_secp256k1_verify` and returns `Ok(Reduction(1300000, nil))`, demonstrating that the secp operation executes despite the flag being absent. The same program using 1-byte opcode `[0x40]` (64) under identical flags returns `EvalErr::Unimplemented`, confirming the asymmetry. [7](#0-6) [3](#0-2)

### Citations

**File:** src/chia_dialect.rs (L62-63)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;
```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L157-183)
```rust
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

**File:** src/chia_dialect.rs (L246-252)
```rust
            62 if flags.contains(ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD) => op_keccak256,
            63 if flags.contains(ClvmFlags::ENABLE_SHA256_TREE) => op_sha256_tree,
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
            _ => {
                return unknown_operator(allocator, o, argument_list, flags, max_cost);
            }
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
