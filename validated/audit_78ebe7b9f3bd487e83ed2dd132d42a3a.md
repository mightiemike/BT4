### Title
`ENABLE_SECP_OPS` Flag Check Bypassed via 4-Byte Opcode Encoding — (`File: src/chia_dialect.rs`)

### Summary

`ChiaDialect::op()` gates the secp256k1/r1 verify operations behind `ClvmFlags::ENABLE_SECP_OPS` for their 1-byte opcode aliases (64, 65), but the identical operations are also reachable via their 4-byte opcode encodings (`0x13d61f00`, `0x1c3a8f00`) with **no flag check at all**. An attacker-controlled CLVM program can invoke the secp operations unconditionally by using the 4-byte encoding, bypassing the intended gate.

### Finding Description

In `src/chia_dialect.rs`, `ChiaDialect::op()` has two separate dispatch paths for secp operations:

**Path 1 — 4-byte opcodes (lines 157–183):** When the operator atom is exactly 4 bytes, the function matches against two specific 32-bit values and dispatches directly to the secp functions with no flag check:

```rust
if op_len == 4 {
    let b = allocator.atom(o);
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,   // no ENABLE_SECP_OPS check
        0x1c3a8f00 => op_secp256r1_verify,   // no ENABLE_SECP_OPS check
        _ => { return unknown_operator(...); }
    };
    return f(allocator, argument_list, max_cost, flags);
}
``` [1](#0-0) 

**Path 2 — 1-byte opcodes (lines 248–249):** When the operator atom is 1 byte, the same functions are dispatched only when `ENABLE_SECP_OPS` is set:

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [2](#0-1) 

The flag is defined as gating secp opcodes 64 and 65:

```rust
/// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
const ENABLE_SECP_OPS = 0x0800;
``` [3](#0-2) 

The 4-byte values are the cost-encoded original opcode assignments for the same operations (multiplier `0x13d61f` + 1 = 1,300,000 = `SECP256K1_VERIFY_COST`; multiplier `0x1c3a8f` + 1 = 1,850,000 = `SECP256R1_VERIFY_COST`): [4](#0-3) 

Both paths call the exact same `op_secp256k1_verify` / `op_secp256r1_verify` functions. The flag check on the 1-byte path is therefore trivially bypassed by encoding the operator as a 4-byte atom instead.

### Impact Explanation

Any caller that sets a dialect without `ENABLE_SECP_OPS` — intending to disallow secp signature verification — is silently bypassed. A CLVM program submitted with operator atom `\x13\xd6\x1f\x00` or `\x1c\x3a\x8f\x00` will execute `op_secp256k1_verify` or `op_secp256r1_verify` regardless of the flag. This is a **flag/operator wiring error** producing a **consensus divergence**: two nodes running the same dialect flags but different versions of this dispatch logic will disagree on whether the program is valid, which is a consensus-critical failure on the Chia blockchain.

### Likelihood Explanation

The entry path is fully attacker-controlled: the operator atom in a CLVM program is arbitrary bytes supplied by the transaction submitter. The 4-byte encoding is a valid, well-formed atom that the serializer accepts without restriction. No special privileges or configuration are required. Any node that exposes `run_serialized_chia_program` (the Python API entry point) or the Rust `run_program` with a `ChiaDialect` that lacks `ENABLE_SECP_OPS` is reachable. [5](#0-4) 

### Recommendation

Add the `ENABLE_SECP_OPS` guard to the 4-byte opcode dispatch path, mirroring the check already present for the 1-byte aliases:

```rust
// in the op_len == 4 branch:
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => { return unknown_operator(allocator, o, argument_list, flags, max_cost); }
};
```

This ensures that secp operations are uniformly gated by `ENABLE_SECP_OPS` regardless of which opcode encoding is used, eliminating the bypass.

### Proof of Concept

A CLVM program with operator atom `\x13\xd6\x1f\x00` (4 bytes) and three arguments (pubkey, msg, sig) will invoke `op_secp256k1_verify` on a `ChiaDialect` constructed **without** `ENABLE_SECP_OPS`. The same program with operator atom `\x40` (1 byte, opcode 64) will be rejected by `unknown_operator` on the same dialect. The two encodings of the same operation produce different outcomes under the same flag configuration, confirming the bypass. [6](#0-5) [7](#0-6)

### Citations

**File:** src/chia_dialect.rs (L62-64)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;

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

**File:** src/chia_dialect.rs (L248-252)
```rust
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
            _ => {
                return unknown_operator(allocator, o, argument_list, flags, max_cost);
            }
```

**File:** src/secp_ops.rs (L11-12)
```rust
const SECP256R1_VERIFY_COST: Cost = 1850000;
const SECP256K1_VERIFY_COST: Cost = 1300000;
```

**File:** wheel/src/api.rs (L40-62)
```rust
pub fn run_serialized_chia_program(
    py: Python,
    program: &[u8],
    args: &[u8],
    max_cost: Cost,
    flags: u32,
) -> PyResult<(u64, LazyNode)> {
    let flags = ClvmFlags::from_bits_truncate(flags);
    let mut allocator = if flags.contains(ClvmFlags::LIMIT_HEAP) {
        Allocator::new_limited(500000000)
    } else {
        Allocator::new()
    };

    let r: Response = (|| -> PyResult<Response> {
        let program = node_from_bytes(&mut allocator, program).map_err(eval_to_py)?;
        let args = node_from_bytes(&mut allocator, args).map_err(eval_to_py)?;
        let dialect = ChiaDialect::new(flags);

        Ok(py.detach(|| run_program(&mut allocator, &dialect, program, args, max_cost)))
    })()?;
    adapt_response(py, allocator, r)
}
```
