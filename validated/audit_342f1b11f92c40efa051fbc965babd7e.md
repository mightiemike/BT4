### Title
4-Byte Secp Opcodes Bypass `ENABLE_SECP_OPS` Flag Guard — (`File: src/chia_dialect.rs`)

---

### Summary

The `ChiaDialect::op()` function in `src/chia_dialect.rs` dispatches the 4-byte secp opcodes `0x13d61f00` (`secp256k1_verify`) and `0x1c3a8f00` (`secp256r1_verify`) directly to their implementations **without checking `ClvmFlags::ENABLE_SECP_OPS`**, while the 1-byte equivalents (opcodes 64 and 65) correctly gate on that flag. An attacker-controlled CLVM program can use the 4-byte opcode forms to invoke secp signature verification in any execution context, regardless of whether the flag is set.

---

### Finding Description

`ChiaDialect::op()` has two separate dispatch paths for secp operations.

**1-byte path (correctly guarded):**
```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [1](#0-0) 

**4-byte path (guard absent):**
```rust
let f = match opcode {
    0x13d61f00 => op_secp256k1_verify,
    0x1c3a8f00 => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
return f(allocator, argument_list, max_cost, flags);
``` [2](#0-1) 

The 4-byte path is entered whenever `op_len == 4`, which is checked before the 1-byte dispatch table is reached. [3](#0-2) 

The `ENABLE_SECP_OPS` flag is documented as the gate for secp opcodes 64 and 65, but the 4-byte forms of the same operations are never checked against it. [4](#0-3) 

The actual secp implementations (`op_secp256k1_verify`, `op_secp256r1_verify`) accept `_flags: ClvmFlags` but ignore it entirely — they perform no internal flag check themselves. [5](#0-4) [6](#0-5) 

---

### Impact Explanation

Any caller that constructs a `ChiaDialect` without `ENABLE_SECP_OPS` (e.g., a node running before the secp hard-fork activates, or a mempool validator using `MEMPOOL_MODE` which does not include `ENABLE_SECP_OPS`) intends to reject secp operations. However, a CLVM program using the 4-byte opcode `0x13d61f00` or `0x1c3a8f00` will still execute `op_secp256k1_verify` / `op_secp256r1_verify` successfully. This produces:

- **Consensus divergence**: nodes that enforce the flag via 1-byte opcodes will reject a program; nodes that encounter the 4-byte form will accept and execute it, producing different chain state.
- **Flag guard bypass**: the `ENABLE_SECP_OPS` flag is rendered ineffective as an access control mechanism — any attacker-controlled program can substitute 4-byte opcodes for 1-byte opcodes to circumvent the intended restriction.

Note that `MEMPOOL_MODE` does not include `ENABLE_SECP_OPS`: [7](#0-6) 

---

### Likelihood Explanation

The 4-byte opcode values are derivable from the cost formula documented in the source comments. Any attacker who reads the source or reverse-engineers the cost encoding can craft a CLVM program using `0x13d61f00` or `0x1c3a8f00` as the operator atom. The entry path is fully attacker-controlled: the operator atom is part of the CLVM program bytes passed to `run_program`.

---

### Recommendation

Add an `ENABLE_SECP_OPS` flag check in the 4-byte dispatch path, mirroring the guard on the 1-byte opcodes:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

This ensures both opcode forms are subject to the same activation condition, eliminating the bypass.

---

### Proof of Concept

1. Construct a `ChiaDialect` with `ClvmFlags::empty()` (no `ENABLE_SECP_OPS`).
2. Build a CLVM program whose operator atom is the 4-byte atom `[0x13, 0xd6, 0x1f, 0x00]` with valid secp256k1 pubkey/msg/sig arguments.
3. Call `run_program`. The 4-byte dispatch path at line 175–182 of `src/chia_dialect.rs` matches `0x13d61f00`, skips the flag check, and calls `op_secp256k1_verify` directly.
4. The secp verification executes and returns `nil` (success) — despite `ENABLE_SECP_OPS` being absent.
5. The same program using 1-byte opcode `64` would fall through to `unknown_operator` and be treated as a no-op or error, demonstrating the divergence. [3](#0-2) [8](#0-7)

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

**File:** src/secp_ops.rs (L15-20)
```rust
pub fn op_secp256r1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
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
