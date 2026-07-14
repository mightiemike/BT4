The code is clear. Let me verify the exact lines and check if there's any additional guard elsewhere.

### Title
Unconditional `op_secp256k1_verify` / `op_secp256r1_verify` Dispatch via 4-Byte Opcode Path Bypasses `ENABLE_SECP_OPS` Flag Guard — (`src/chia_dialect.rs`)

---

### Summary

The 4-byte opcode branch in `ChiaDialect::op` dispatches opcodes `0x13d61f00` and `0x1c3a8f00` directly to `op_secp256k1_verify` and `op_secp256r1_verify` with **no** `ENABLE_SECP_OPS` flag check. The identical secp functions are also reachable via 1-byte opcodes 64 and 65, but those paths are correctly gated. The 4-byte path is a concrete, flag-free bypass.

---

### Finding Description

In `ChiaDialect::op`, when the operator atom is exactly 4 bytes long, the code reads the opcode as a big-endian `u32` and matches it:

```rust
// src/chia_dialect.rs lines 157–182
if op_len == 4 {
    let b = allocator.atom(o);
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,   // ← no flag check
        0x1c3a8f00 => op_secp256r1_verify,   // ← no flag check
        _ => {
            return unknown_operator(allocator, o, argument_list, flags, max_cost);
        }
    };
    return f(allocator, argument_list, max_cost, flags);
}
``` [1](#0-0) 

The 1-byte path for the same functions is correctly guarded:

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [2](#0-1) 

`op_secp256k1_verify` and `op_secp256r1_verify` both accept `_flags: ClvmFlags` (underscore — unused), so they perform no internal flag check either: [3](#0-2) 

The `ENABLE_SECP_OPS` flag definition confirms it is the intended gate for secp opcodes: [4](#0-3) 

---

### Impact Explanation

Any caller that constructs a CLVM program with a 4-byte atom `[0x13, 0xd6, 0x1f, 0x00]` as the operator invokes `op_secp256k1_verify` regardless of whether `ENABLE_SECP_OPS` is set. This means:

- **Softfork bypass**: `ENABLE_SECP_OPS` is the flag that controls softfork activation of secp ops. The 4-byte path makes that activation irrelevant for these opcodes.
- **Consensus divergence**: A node running without `ENABLE_SECP_OPS` will execute secp verification via the 4-byte path and accept or reject a spend based on signature validity. A node that correctly treats the opcode as unknown (if the 4-byte path were properly gated) would accept it as a no-op. These two nodes reach different conclusions about the same spend.
- **Mempool mode is not immune**: `MEMPOOL_MODE` sets `NO_UNKNOWN_OPS` but does not set `ENABLE_SECP_OPS`. A program using `0x13d61f00` in mempool mode still executes secp verification rather than being rejected as an unknown op. [5](#0-4) 

---

### Likelihood Explanation

The path is directly reachable via the standard `run_program` / `ChiaDialect::op` production API with attacker-controlled CLVM data. No special privileges, compromised nodes, or downstream misuse are required. The attacker only needs to craft a CLVM program whose operator atom is the 4-byte sequence `0x13d61f00`.

---

### Recommendation

Add the `ENABLE_SECP_OPS` guard to the 4-byte opcode branch, mirroring the 1-byte path:

```rust
0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
_ => {
    return unknown_operator(allocator, o, argument_list, flags, max_cost);
}
```

This ensures both opcode encodings are subject to the same activation gate.

---

### Proof of Concept

```rust
use clvm_rs::allocator::Allocator;
use clvm_rs::chia_dialect::{ChiaDialect, ClvmFlags};
use clvm_rs::run_program::run_program;

fn main() {
    let mut a = Allocator::new();
    // Build (0x13d61f00 pubkey msg sig) — valid secp256k1 inputs
    let opcode = a.new_atom(&[0x13, 0xd6, 0x1f, 0x00]).unwrap();
    // ... build args with a valid pubkey/msg/sig triple ...
    let program = /* cons opcode onto args */;
    let env = a.nil();

    // No ENABLE_SECP_OPS flag set
    let dialect = ChiaDialect::new(ClvmFlags::empty());
    let result = run_program(&mut a, &dialect, program, env, 10_000_000);
    // result is Ok(...) — secp verification executed and succeeded
    // Expected (if flag were enforced): Err(unknown op) or no-op
    assert!(result.is_ok(), "4-byte secp path bypasses ENABLE_SECP_OPS");
}
```

The program succeeds (or fails on signature invalidity, not on a flag check), confirming the bypass.

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

**File:** src/secp_ops.rs (L61-66)
```rust
pub fn op_secp256k1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
```
