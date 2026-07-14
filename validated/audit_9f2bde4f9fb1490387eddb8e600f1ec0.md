### Title
4-Byte Secp Opcode Dispatch Bypasses `ENABLE_SECP_OPS` Flag Gate — (File: `src/chia_dialect.rs`)

---

### Summary

The `ChiaDialect::op()` function in `src/chia_dialect.rs` dispatches the 4-byte opcode forms of `secp256k1_verify` (`0x13d61f00`) and `secp256r1_verify` (`0x1c3a8f00`) directly to their operator implementations without checking the `ENABLE_SECP_OPS` flag. The 1-byte opcode forms (opcodes 64 and 65) correctly gate on this flag. An attacker-controlled CLVM program can invoke secp signature verification via the 4-byte opcode path regardless of whether `ENABLE_SECP_OPS` is set, bypassing the intended hard-fork gate.

---

### Finding Description

`ChiaDialect::op()` handles two opcode-length branches:

**1-byte opcode path (lines 248–249) — correctly gated:**
```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [1](#0-0) 

If `ENABLE_SECP_OPS` is absent, opcodes 64 and 65 fall through to `unknown_operator`, which returns `EvalErr::Unimplemented` in mempool mode or a nil no-op in consensus mode.

**4-byte opcode path (lines 175–182) — flag check missing:**
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

No `ENABLE_SECP_OPS` check exists here. The secp operator executes unconditionally for any caller, regardless of dialect flags.

The `ENABLE_SECP_OPS` flag is defined as a hard-fork gate:
```rust
/// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
const ENABLE_SECP_OPS = 0x0800;
``` [3](#0-2) 

The flag's stated purpose is to prevent secp verification from executing before the hard fork activates. The 4-byte opcode path renders this gate ineffective.

The existing test suite confirms the 1-byte path is gated (tests for opcodes 64 and 65 without `ENABLE_SECP_OPS` expect `"unimplemented operator"`), but there are no analogous tests for the 4-byte opcode forms without the flag: [4](#0-3) 

---

### Impact Explanation

**Consensus divergence.** Before the secp hard fork activates, nodes are expected to treat secp opcodes as unknown (returning nil in consensus mode, or erroring in mempool mode). A CLVM puzzle using the 4-byte opcode `0x13d61f00` or `0x1c3a8f00` will:

- On a node running this code (without `ENABLE_SECP_OPS`): execute real secp signature verification and return nil on success, or raise on failure.
- On a node that treats the 4-byte opcode as a plain unknown op (e.g., an older version or a node where the 4-byte dispatch was not added): return nil unconditionally with a cost derived from the unknown-op formula.

These two behaviors are semantically different. A puzzle that raises on secp failure would pass on one node and fail on another, splitting consensus. The `ENABLE_SECP_OPS` flag is the intended mechanism to prevent this split, but it is not consulted in the 4-byte path.

---

### Likelihood Explanation

**Medium.** The 4-byte opcode forms are less commonly used than the named 1-byte forms, but they are fully reachable via attacker-controlled CLVM bytes submitted as puzzle programs. No special privileges are required — any coin spend can embed a 4-byte opcode. The bypass is silent: no error is raised, and the operator executes normally. The gap is not covered by any existing test case.

---

### Recommendation

Add the `ENABLE_SECP_OPS` flag check to the 4-byte opcode dispatch path, mirroring the 1-byte path:

```rust
// In the op_len == 4 branch:
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

Add test cases that verify the 4-byte opcode forms return `"unimplemented operator"` (mempool mode) or nil (consensus mode) when `ENABLE_SECP_OPS` is not set, consistent with the existing tests for opcodes 64 and 65.

---

### Proof of Concept

Construct a CLVM program that uses the raw 4-byte opcode `0x13d61f00` with a valid secp256k1 pubkey, 32-byte message digest, and signature. Run it with `ChiaDialect::new(ClvmFlags::NO_UNKNOWN_OPS)` (no `ENABLE_SECP_OPS`):

- **Expected (per flag semantics):** `EvalErr::Unimplemented` — the operator should be unavailable before the hard fork.
- **Actual:** `op_secp256k1_verify` executes; returns `Ok(Reduction(1300000, nil))` on a valid signature, or `EvalErr::Secp256Failed` on an invalid one.

The 4-byte opcode bypasses the flag gate that the 1-byte opcode 64 correctly enforces, directly analogous to `amoMinterBorrow` bypassing `recollateralizePaused` and `collateralEnabled` checks that other functions in the same contract correctly enforce.

### Citations

**File:** src/chia_dialect.rs (L62-63)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;
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

**File:** src/run_program.rs (L1374-1382)
```rust
        // Opcode 65 without ENABLE_SECP_OPS is unimplemented
        RunProgramTest {
            prg: "(secp256r1_verify_65 (q . 0x0437a1674f3883b7171a11a20140eee014947b433723cf9f181a18fee4fcf96056103b3ff2318f00cca605e6f361d18ff0d2d6b817b1fa587e414f8bb1ab60d2b9) (q . 0x9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08) (q . 0xe8de121f4cceca12d97527cc957cca64a4bcfc685cffdee051b38ee81cb22d7e2c187fec82c731018ed2d56f08a4a5cbc40c5bfe9ae18c02295bb65e7f605ffc))",
            args: "()",
            flags: ClvmFlags::NO_UNKNOWN_OPS,
            result: None,
            cost: 0,
            err: "unimplemented operator",
        },
```
