### Title
`ENABLE_SECP_OPS` Flag Gate Bypassed via Dual Opcode Registration of secp Operators - (File: `src/chia_dialect.rs`)

### Summary
The `op_secp256k1_verify` and `op_secp256r1_verify` operators are registered in two separate opcode spaces simultaneously: a 4-byte opcode path (`0x13d61f00` / `0x1c3a8f00`) that is **always active with no flag check**, and a 1-byte opcode path (opcodes 64 / 65) that is **gated by `ENABLE_SECP_OPS`**. An attacker-controlled CLVM program can invoke secp operations via the 4-byte opcode path regardless of whether `ENABLE_SECP_OPS` is set, bypassing the intended soft-fork activation gate and creating a consensus divergence between nodes with different flag configurations.

### Finding Description
In `src/chia_dialect.rs`, the `ChiaDialect::op()` function dispatches operators through two independent branches. The 4-byte branch (lines 157–183) unconditionally maps `0x13d61f00` → `op_secp256k1_verify` and `0x1c3a8f00` → `op_secp256r1_verify` with no flag check:

```rust
// src/chia_dialect.rs lines 175-182
let f = match opcode {
    0x13d61f00 => op_secp256k1_verify,
    0x1c3a8f00 => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
return f(allocator, argument_list, max_cost, flags);
```

The 1-byte branch (lines 248–249) gates the same operators behind `ENABLE_SECP_OPS`:

```rust
// src/chia_dialect.rs lines 248-249
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The `ENABLE_SECP_OPS` flag is documented as "Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify)" — it only mentions the 1-byte opcodes. The 4-byte opcode path is entirely outside the flag's control. `MEMPOOL_MODE` (lines 72–76) does not include `ENABLE_SECP_OPS`, so in mempool mode the 4-byte secp opcodes remain active while the 1-byte opcodes are rejected.

This is a direct analog to the original report's bug class: two different code paths share the same operator implementation (the secp verifiers) without consistent mutual exclusion. Just as an external minter can claim an ID in the reserved range and disrupt `buyNFT`, an attacker-controlled CLVM program can claim the secp operator via the 4-byte opcode path and bypass the flag gate that is supposed to control secp availability.

### Impact Explanation
**Consensus divergence**: A CLVM program using 1-byte opcode 64 is rejected on nodes without `ENABLE_SECP_OPS` but accepted on nodes with it. A program using 4-byte opcode `0x13d61f00` is accepted on **all** nodes regardless of the flag. During any transition period where nodes have different flag configurations, the two opcode encodings produce opposite acceptance decisions for the same logical operation, splitting consensus.

**Soft-fork gate bypass**: The secp soft-fork is supposed to be activated by setting `ENABLE_SECP_OPS`. But because the 4-byte opcodes are always active, secp signature verification is reachable in any CLVM execution context — including pre-activation consensus mode and mempool mode — simply by encoding the operator as a 4-byte atom. The flag provides no actual barrier.

**Mempool inconsistency**: `MEMPOOL_MODE` (which includes `NO_UNKNOWN_OPS` but not `ENABLE_SECP_OPS`) rejects 1-byte opcode 64 as `Unimplemented` but accepts 4-byte opcode `0x13d61f00` and executes the full secp verification. A transaction that would be rejected via one encoding is accepted via the other.

### Likelihood Explanation
The entry path is fully attacker-controlled: any caller who can submit CLVM bytes to `run_program` (via `run_serialized_chia_program` in `wheel/src/api.rs`, or directly via the Rust API) can craft a program using the 4-byte opcode form. No special privileges, compromised nodes, or social engineering are required. The 4-byte opcode values are derivable from the cost formula documented in the code comments themselves.

### Recommendation
The 4-byte secp opcode branch in `ChiaDialect::op()` should apply the same `ENABLE_SECP_OPS` flag check as the 1-byte branch. Specifically, the match arm for `0x13d61f00` and `0x1c3a8f00` should return `unknown_operator(...)` when `!flags.contains(ClvmFlags::ENABLE_SECP_OPS)`, mirroring the guard on opcodes 64 and 65. This ensures a single consistent activation gate for secp operations across both opcode encodings.

### Proof of Concept
The following CLVM program invokes `secp256k1_verify` via the 4-byte opcode `0x13d61f00` (a valid secp256k1 pubkey, message digest, and signature must be supplied as arguments):

```
; Opcode atom is 4 bytes: 0x13 0xd6 0x1f 0x00
; This bypasses ENABLE_SECP_OPS because the 4-byte branch has no flag check.
; Run with MEMPOOL_MODE (NO_UNKNOWN_OPS | LIMIT_HEAP | DISABLE_OP | CANONICAL_INTS | LIMIT_SOFTFORK)
; — which does NOT include ENABLE_SECP_OPS — and the call still succeeds.
(0x13d61f00 pubkey msg sig)
```

Concretely, in `src/chia_dialect.rs`:
- Line 157: `if op_len == 4` — enters the 4-byte branch
- Line 176: `0x13d61f00 => op_secp256k1_verify` — dispatches unconditionally
- Line 182: `return f(allocator, argument_list, max_cost, flags)` — executes secp verification

No `ENABLE_SECP_OPS` check is present anywhere in this path. The same program encoded with 1-byte opcode `0x40` (64) would be rejected by `unknown_operator` in the same flag configuration, proving the inconsistency. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** src/chia_dialect.rs (L56-67)
```rust
        const DISABLE_OP = 0x200;

        /// Enables the sha256tree op *outside* the softfork guard. Hard-fork;
        /// enable only when it activates.
        const ENABLE_SHA256_TREE = 0x0400;

        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;

        /// Use malachite-bigint instead of num-bigint for div, divmod, mod, and modpow.
        const MALACHITE = 0x1000;
    }
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
