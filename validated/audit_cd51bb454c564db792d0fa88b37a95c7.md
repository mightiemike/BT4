### Title
`ENABLE_SECP_OPS` Flag Not Checked for 4-Byte Secp Opcode Forms, Bypassing Hard-Fork Gate - (File: `src/chia_dialect.rs`)

### Summary

The `ChiaDialect::op` function in `src/chia_dialect.rs` gates the 1-byte secp opcodes (64 = `secp256k1_verify`, 65 = `secp256r1_verify`) behind the `ENABLE_SECP_OPS` flag, but the equivalent 4-byte opcode forms (`0x13d61f00` and `0x1c3a8f00`) are dispatched to the same secp verification functions **without any flag check**. An attacker-controlled CLVM program can use the 4-byte opcode encoding to invoke secp signature verification regardless of whether `ENABLE_SECP_OPS` is set, bypassing the hard-fork activation gate entirely.

### Finding Description

In `src/chia_dialect.rs`, the `op` method of `ChiaDialect` dispatches operators in two separate branches: one for 1-byte opcodes and one for 4-byte opcodes.

The 1-byte branch correctly gates secp operations behind `ENABLE_SECP_OPS`:

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [1](#0-0) 

However, the 4-byte branch dispatches the same functions with **no flag check**:

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

The `ENABLE_SECP_OPS` flag is documented as the hard-fork gate for secp opcodes: [3](#0-2) 

The `MEMPOOL_MODE` constant does not include `ENABLE_SECP_OPS`: [4](#0-3) 

Before the 4-byte opcodes were explicitly mapped, they fell through to `op_unknown`, which returns `nil` with a cost in lenient mode and is rejected in strict mode (`NO_UNKNOWN_OPS`). Now they invoke actual secp cryptographic verification regardless of the flag state.

### Impact Explanation

There are two concrete broken invariants:

1. **Hard-fork gate bypass**: The `ENABLE_SECP_OPS` flag is intended to activate secp operations only after a hard fork. Any CLVM program using the 4-byte opcode encoding (`0x13d61f00` / `0x1c3a8f00`) invokes real secp verification even when the flag is absent. A program that should fail (because secp is not yet activated) instead succeeds or fails based on the cryptographic result — a different outcome than the intended `nil`/unknown-op behavior.

2. **Mempool/consensus divergence**: In `MEMPOOL_MODE` (which includes `NO_UNKNOWN_OPS` but not `ENABLE_SECP_OPS`), the 4-byte secp opcodes were previously rejected as unknown operators. After this explicit mapping, they are accepted and execute real secp verification. A program using 4-byte secp opcodes can now pass mempool validation that it should not, creating a split between mempool and consensus behavior.

The corrupted result is the `Response` value: instead of `Err(EvalErr::Unimplemented)` (strict mode) or `Ok(Reduction(cost, nil))` (lenient mode), the program receives the actual secp verification outcome.

### Likelihood Explanation

The entry path is direct: any attacker who can submit a CLVM program (e.g., a spend bundle to the mempool) can craft a program using the 4-byte opcode encoding. The 4-byte opcodes are valid CLVM atoms and are fully attacker-controlled. No special privileges are required. The bypass is unconditional — it applies to every invocation of the 4-byte secp opcodes regardless of dialect configuration.

### Recommendation

Add the `ENABLE_SECP_OPS` flag check in the 4-byte opcode branch, mirroring the 1-byte branch:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

This ensures that when `ENABLE_SECP_OPS` is not set, the 4-byte secp opcodes fall through to `unknown_operator`, preserving the pre-hard-fork behavior (nil in lenient mode, error in strict mode).

### Proof of Concept

1. Construct a CLVM program that uses the 4-byte opcode `0x13d61f00` (secp256k1_verify) with a valid pubkey, message digest, and signature.
2. Run it with `ChiaDialect::new(ClvmFlags::empty())` — no `ENABLE_SECP_OPS` set.
3. Observe that `run_program` dispatches to `op_secp256k1_verify` and returns `Ok(Reduction(1300000, nil))` on a valid signature, instead of the expected `Ok(Reduction(1300000, nil))` from `op_unknown` (same cost, but different semantic: the unknown-op path does not validate the signature and always succeeds, while the secp path fails on an invalid signature).
4. Repeat with an invalid signature: `op_unknown` returns `Ok`, but the 4-byte secp path returns `Err(EvalErr::Secp256Failed)` — a consensus-observable difference.
5. Repeat with `ClvmFlags::NO_UNKNOWN_OPS` (mempool mode, no `ENABLE_SECP_OPS`): previously the 4-byte opcode would return `Err(EvalErr::Unimplemented)`; now it executes the secp function and returns based on signature validity.

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

**File:** src/chia_dialect.rs (L175-182)
```rust
            let f = match opcode {
                0x13d61f00 => op_secp256k1_verify,
                0x1c3a8f00 => op_secp256r1_verify,
                _ => {
                    return unknown_operator(allocator, o, argument_list, flags, max_cost);
                }
            };
            return f(allocator, argument_list, max_cost, flags);
```

**File:** src/chia_dialect.rs (L248-249)
```rust
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```
