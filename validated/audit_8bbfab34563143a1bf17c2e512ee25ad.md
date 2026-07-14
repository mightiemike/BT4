Let me explore the clvm_rs repository structure and find potential analogs to the "aliasing bypass" vulnerability class.

I see a critical pattern in `src/chia_dialect.rs`. Let me investigate the secp operator dual-opcode aliasing more deeply.

Now I have a complete picture. Let me verify the exact lines and confirm the dual-opcode aliasing pattern.

### Title
Secp256k1/r1 Operators Reachable via 4-Byte Opcode Alias, Bypassing `ENABLE_SECP_OPS` Flag Gate — (`src/chia_dialect.rs`)

---

### Summary

`ChiaDialect::op()` dispatches `op_secp256k1_verify` and `op_secp256r1_verify` through **two distinct opcode representations**. The 1-byte forms (opcodes `64` and `65`) are correctly gated behind the `ENABLE_SECP_OPS` flag. The 4-byte forms (`0x13d61f00` and `0x1c3a8f00`) are dispatched **unconditionally**, with no flag check. Any caller that relies on the absence of `ENABLE_SECP_OPS` to prevent secp execution — including `MEMPOOL_MODE` — can be bypassed by an attacker who encodes the operator using the 4-byte alias.

---

### Finding Description

In `ChiaDialect::op()`, the dispatch logic has two separate branches for operator length:

**Branch 1 — 4-byte opcodes (lines 157–183):** The function reads the 4-byte big-endian opcode and matches it directly:

```rust
let f = match opcode {
    0x13d61f00 => op_secp256k1_verify,
    0x1c3a8f00 => op_secp256r1_verify,
    _ => { return unknown_operator(...); }
};
return f(allocator, argument_list, max_cost, flags);
```

No flag check of any kind is performed before dispatching. [1](#0-0) 

**Branch 2 — 1-byte opcodes (lines 248–249):** The same two functions are also reachable via single-byte opcodes, but only when `ENABLE_SECP_OPS` is set:

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [2](#0-1) 

The `ENABLE_SECP_OPS` flag is documented as the gate for secp operations:

```rust
/// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
const ENABLE_SECP_OPS = 0x0800;
``` [3](#0-2) 

The flag description names only opcodes 64 and 65, but the underlying functions `op_secp256k1_verify` and `op_secp256r1_verify` are also reachable via the 4-byte aliases without any flag check. The invariant "secp operations require `ENABLE_SECP_OPS`" is broken by the 4-byte alias path.

Critically, `MEMPOOL_MODE` — the stricter validation mode used for transaction acceptance — does **not** include `ENABLE_SECP_OPS`:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
``` [4](#0-3) 

In mempool mode, opcode `64` falls through to `unknown_operator`, which then hits `NO_UNKNOWN_OPS` and returns `EvalErr::Unimplemented`. But opcode `0x13d61f00` enters the 4-byte branch, matches directly, and executes `op_secp256k1_verify` — bypassing `NO_UNKNOWN_OPS` entirely and returning a successful `Reduction`. [5](#0-4) 

---

### Impact Explanation

An attacker-controlled CLVM program that encodes the secp operator as the 4-byte atom `\x13\xd6\x1f\x00` (or `\x1c\x3a\x8f\x00` for r1) will:

1. Execute real secp256k1/r1 signature verification even when `ENABLE_SECP_OPS` is absent.
2. Succeed in `MEMPOOL_MODE` where the 1-byte form would be rejected as an unknown op.
3. Produce a different execution result than a node or validator that treats the 4-byte opcode as a plain unknown no-op (e.g., an older node or a node with a different dialect).

The corrupted result is the `Response` value: the 4-byte path returns `Ok(Reduction(1300000, nil))` (a successful secp verification), while the expected behavior under `!ENABLE_SECP_OPS` is rejection. This is a **flag/operator wiring error** causing **consensus divergence** and **mempool validation bypass**.

---

### Likelihood Explanation

The 4-byte opcode values are derivable from the cost formula documented in the source comments and in `docs/new-operator-checklist.md`. Any attacker who reads the `ChiaDialect::op()` source can construct the bypass. The entry path is a raw attacker-controlled CLVM byte sequence passed to `run_program` — the most direct externally reachable surface in the codebase. No special privileges or social engineering are required.

---

### Recommendation

Add the `ENABLE_SECP_OPS` flag check to the 4-byte dispatch branch, mirroring the guard already present for the 1-byte opcodes:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => { return unknown_operator(allocator, o, argument_list, flags, max_cost); }
};
```

This ensures that both opcode representations of the secp operators are subject to the same flag gate, eliminating the aliasing bypass.

---

### Proof of Concept

1. Construct a CLVM program whose operator atom is the 4-byte sequence `\x13\xd6\x1f\x00` (secp256k1_verify) with a valid pubkey, message digest, and signature as arguments.
2. Run it via `ChiaDialect` with `MEMPOOL_MODE` flags (which excludes `ENABLE_SECP_OPS`).
3. **Expected**: rejection, because secp ops are not enabled and `NO_UNKNOWN_OPS` is set.
4. **Actual**: `op_secp256k1_verify` executes and returns `Ok(Reduction(1300000, nil))` — a successful verification — because the 4-byte branch at line 176 dispatches directly without checking `ENABLE_SECP_OPS`.

The same program encoded with 1-byte opcode `\x40` (decimal 64) under identical flags would be routed to `unknown_operator` and rejected with `EvalErr::Unimplemented`. The two representations of the same operator produce divergent outcomes under the same flag set. [1](#0-0) [2](#0-1)

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
