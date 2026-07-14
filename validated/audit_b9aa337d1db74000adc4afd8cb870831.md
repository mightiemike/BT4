### Title
Missing `ENABLE_SECP_OPS` Flag Check in 4-Byte Opcode Dispatch Path — (`src/chia_dialect.rs`)

### Summary

The 4-byte opcode dispatch branch in `ChiaDialect::op` unconditionally invokes `op_secp256k1_verify` and `op_secp256r1_verify` when the operator atom matches `0x13d61f00` or `0x1c3a8f00`, with no check for the `ENABLE_SECP_OPS` flag. The 1-byte opcode path (opcodes 64 and 65) correctly gates these same functions behind `ENABLE_SECP_OPS`. An attacker supplying a 4-byte operator atom bypasses the flag entirely.

### Finding Description

In `ChiaDialect::op`, the dispatch logic splits on `op_len`:

**4-byte path — no flag check:** [1](#0-0) 

```rust
let op_len = allocator.atom_len(o);
if op_len == 4 {
    let b = allocator.atom(o);
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,   // ← no ENABLE_SECP_OPS check
        0x1c3a8f00 => op_secp256r1_verify,   // ← no ENABLE_SECP_OPS check
        _ => { return unknown_operator(...); }
    };
    return f(allocator, argument_list, max_cost, flags);
}
```

**1-byte path — flag correctly checked:** [2](#0-1) 

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The `ENABLE_SECP_OPS` flag is defined at: [3](#0-2) 

The attacker-controlled operator atom `[0x13, 0xd6, 0x1f, 0x00]` (4 bytes) is stored as a `Bytes` node (not a `SmallAtom`) because `fits_in_small_atom` rejects it — the first byte `0x13 > 0x03` fails the 4-byte small-atom constraint: [4](#0-3) 

`atom_len` on this `Bytes` node returns 4, entering the unguarded branch. `atom()` returns the raw bytes `[0x13, 0xd6, 0x1f, 0x00]`, which `u32::from_be_bytes` decodes as `0x13d61f00`, matching the secp256k1 arm: [5](#0-4) 

`op_secp256k1_verify` and `op_secp256r1_verify` accept `_flags: ClvmFlags` but ignore it entirely — they perform no internal flag check: [6](#0-5) [7](#0-6) 

### Impact Explanation

A CLVM program using the 4-byte operator `0x13d61f00` or `0x1c3a8f00` will execute secp signature verification under `ChiaDialect::new(ClvmFlags::empty())` — a configuration that explicitly does not enable secp ops. Nodes running with `ENABLE_SECP_OPS` unset will accept or reject such programs differently depending on whether they reach the 1-byte or 4-byte dispatch path, causing **consensus divergence**. The secp ops are also expensive (1,300,000 and 1,850,000 cost units respectively), so this also enables their use in contexts where they should be disallowed by policy.

### Likelihood Explanation

The attack requires only crafting a CLVM program with a 4-byte operator atom — a trivially attacker-controlled input through any public CLVM execution API (`run_program`, Python wheel bindings, etc.). No special privileges or internal access are needed.

### Recommendation

Add the `ENABLE_SECP_OPS` guard to the 4-byte dispatch path, mirroring the 1-byte path:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => { return unknown_operator(allocator, o, argument_list, flags, max_cost); }
};
```

### Proof of Concept

```rust
use clvm_rs::allocator::Allocator;
use clvm_rs::chia_dialect::{ChiaDialect, ClvmFlags};
use clvm_rs::run_program::run_program;

let mut allocator = Allocator::new();
// Operator atom: 4 bytes = 0x13d61f00 (secp256k1_verify)
let op_atom = allocator.new_atom(&[0x13, 0xd6, 0x1f, 0x00]).unwrap();
// ... build a minimal CLVM program invoking this operator ...
let dialect = ChiaDialect::new(ClvmFlags::empty()); // ENABLE_SECP_OPS NOT set
// Expect: rejected as unknown op. Actual: op_secp256k1_verify is called.
```

The 1-byte equivalent (opcode 64) under the same dialect correctly returns an `Unimplemented` error. The 4-byte path does not.

### Citations

**File:** src/chia_dialect.rs (L62-63)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;
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

**File:** src/allocator.rs (L317-340)
```rust
pub fn fits_in_small_atom(v: &[u8]) -> Option<u32> {
    if !v.is_empty()
        && (v.len() > 4
        || (v.len() == 1 && v[0] == 0)
        // a 1-byte buffer of 0 is not the canonical representation of 0
        || (v[0] & 0x80) != 0
        // if the top bit is set, it's a negative number (i.e. not positive)
        || (v[0] == 0 && (v[1] & 0x80) == 0)
        // if the buffer is 4 bytes, the top byte can't use more than 2 bits.
        // otherwise the integer won't fit in 26 bits
        || (v.len() == 4 && v[0] > 0x03))
    {
        // if the top byte is a 0 but the top bit of the next byte is not set,
        // that's a redundant leading zero. i.e. not canonical representation
        None
    } else {
        let mut ret: u32 = 0;
        for b in v {
            ret <<= 8;
            ret |= *b as u32;
        }
        Some(ret)
    }
}
```

**File:** src/allocator.rs (L1038-1054)
```rust
    pub fn atom_len(&self, node: NodePtr) -> usize {
        #[cfg(feature = "allocator-debug")]
        self.validate_node(node);

        let index = node.index();

        match node.object_type() {
            ObjectType::Bytes => {
                let atom = self.atom_vec[index as usize];
                (atom.end - atom.start) as usize
            }
            ObjectType::SmallAtom => len_for_value(index),
            _ => {
                panic!("expected atom, got pair");
            }
        }
    }
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

**File:** src/secp_ops.rs (L61-66)
```rust
pub fn op_secp256k1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
```
