### Title
Secp256 Operator Flag Guard Missing in 4-Byte Opcode Dispatch Path — (`File: src/chia_dialect.rs`)

### Summary
The `ChiaDialect::op()` function in `src/chia_dialect.rs` contains two separate dispatch paths for secp256k1/secp256r1 verification operators. The 1-byte opcode path (opcodes 64 and 65) correctly gates execution behind the `ENABLE_SECP_OPS` hard-fork flag. The 4-byte opcode path (`0x13d61f00` and `0x1c3a8f00`) dispatches directly to the same secp functions **without checking `ENABLE_SECP_OPS`**. This is a concrete flag/operator wiring error: the validation guard is scattered across two code paths and is absent from one of them.

### Finding Description

In `src/chia_dialect.rs`, the `op()` function has two distinct dispatch paths for secp operations:

**Path 1 — 4-byte opcode (no flag check):** [1](#0-0) 

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

**Path 2 — 1-byte opcode (flag check present):** [2](#0-1) 

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The `ENABLE_SECP_OPS` flag is defined as a hard-fork activation gate: [3](#0-2) 

The 4-byte opcodes `0x13d61f00` and `0x1c3a8f00` are the "unknown operator with assigned cost" encoding of the secp operators. Their cost multipliers are designed so that when treated as unknown operators, they produce the same cost as the actual secp functions (1300000 and 1850000 respectively). Before the hard fork, they are supposed to be no-ops with cost. Instead, the code dispatches them to `op_secp256k1_verify` and `op_secp256r1_verify` unconditionally.

The actual secp functions: [4](#0-3) 

`MEMPOOL_MODE` does not include `ENABLE_SECP_OPS`, confirming the flag is intended to be off by default: [5](#0-4) 

### Impact Explanation

When `ENABLE_SECP_OPS` is not set (pre-fork / mempool mode), the 4-byte opcode form of the secp operators bypasses the flag guard and executes the actual cryptographic verification. If an attacker supplies invalid secp arguments (bad pubkey, wrong-length message, or malformed signature), `op_secp256k1_verify` / `op_secp256r1_verify` returns `EvalErr::Secp256Failed` or `EvalErr::InvalidOpArg`, causing the CLVM program to fail. On a reference implementation that correctly treats these 4-byte opcodes as unknown operators (no-ops with cost), the same program succeeds. This is a **consensus divergence**: this node rejects a coin spend that the network accepts, or vice versa, depending on which side of the fork the node is on.

The corrupted result is: `EvalErr::Secp256Failed` (or `InvalidOpArg`) instead of `Ok(Reduction(1300000, nil))`.

### Likelihood Explanation

The entry path is fully attacker-controlled: any CLVM program submitted to the mempool or included in a block can use the 4-byte opcode `0x13d61f00` with deliberately invalid secp arguments (e.g., a 33-byte pubkey that is not on the curve, or a 64-byte signature that does not parse). No special privileges are required. The attacker only needs to craft valid CLVM bytes, which is the normal attack surface for this VM.

### Recommendation

Add the `ENABLE_SECP_OPS` flag check to the 4-byte opcode dispatch path, mirroring the guard already present for the 1-byte opcodes:

```rust
if op_len == 4 {
    let b = allocator.atom(o);
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
        0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
        _ => { return unknown_operator(allocator, o, argument_list, flags, max_cost); }
    };
    return f(allocator, argument_list, max_cost, flags);
}
```

When `ENABLE_SECP_OPS` is not set, the 4-byte opcodes fall through to `unknown_operator`, which correctly returns `Ok(Reduction(cost, nil))` in lenient mode or `Err(Unimplemented)` in strict mode — matching the pre-fork consensus behavior.

### Proof of Concept

1. Construct a CLVM program whose operator atom is the 4-byte sequence `[0x13, 0xd6, 0x1f, 0x00]` with three arguments: a 33-byte atom that is not a valid secp256k1 point, a 32-byte atom, and a 64-byte atom.
2. Run it with `ChiaDialect::new(ClvmFlags::empty())` (no `ENABLE_SECP_OPS`).
3. Observe: `op_secp256k1_verify` executes and returns `EvalErr::InvalidOpArg` ("pubkey is not valid").
4. On a correct pre-fork implementation, the same program returns `Ok(Reduction(1300000, nil))` because the 4-byte opcode is an unknown operator no-op.

The divergence is triggered by attacker-controlled CLVM bytes, requires no special access, and produces a concrete wrong result (`EvalErr` vs. `Ok`) that would cause this node to disagree with the network on coin spend validity.

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

**File:** src/chia_dialect.rs (L248-249)
```rust
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

**File:** src/secp_ops.rs (L15-57)
```rust
pub fn op_secp256r1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let cost = SECP256R1_VERIFY_COST;
    check_cost(cost, max_cost)?;

    let [pubkey, msg, sig] = get_args::<3>(a, input, "secp256r1_verify")?;

    // first argument is sec1 encoded pubkey
    let pubkey = atom(a, pubkey, "secp256r1_verify pubkey")?;
    let verifier = P1VerifyingKey::from_sec1_bytes(pubkey.as_ref()).map_err(|_| {
        EvalErr::InvalidOpArg(input, "secp256r1_verify: pubkey is not valid".to_string())
    })?;

    // second arg is sha256 hash of message
    let msg = atom(a, msg, "secp256r1_verify msg")?;
    if msg.as_ref().len() != 32 {
        Err(EvalErr::InvalidOpArg(
            input,
            "secp256r1_verify: message digest is not 32 bytes".to_string(),
        ))?;
    }

    // third arg is a fixed-size signature
    let sig = atom(a, sig, "secp256r1_verify sig")?;
    let sig = P1Signature::from_slice(sig.as_ref()).map_err(|_| {
        EvalErr::InvalidOpArg(
            input,
            "secp256r1_verify: signature is not valid".to_string(),
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
