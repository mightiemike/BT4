### Title
`ENABLE_SECP_OPS` Flag Bypassed via 4-Byte Opcode Dispatch Path — (`File: src/chia_dialect.rs`)

---

### Summary

`ChiaDialect::op()` in `src/chia_dialect.rs` contains two separate dispatch paths for the secp signature-verification operators. The 1-byte opcode path (opcodes 64 and 65) correctly gates execution behind the `ENABLE_SECP_OPS` flag. The 4-byte opcode path (opcodes `0x13d61f00` and `0x1c3a8f00`) dispatches to the same `op_secp256k1_verify` and `op_secp256r1_verify` functions with **no flag check at all**. An attacker who submits CLVM bytecode using the 4-byte opcode encoding can invoke secp signature verification on any node regardless of whether `ENABLE_SECP_OPS` is set, violating the hard-fork activation invariant and causing consensus divergence.

---

### Finding Description

`ENABLE_SECP_OPS` is documented as the hard-fork activation flag that controls whether secp operators are available:

```
/// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
const ENABLE_SECP_OPS = 0x0800;
``` [1](#0-0) 

The 1-byte opcode dispatch path correctly enforces this flag:

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [2](#0-1) 

However, the 4-byte opcode dispatch path — which handles the legacy encoding of these same operators — performs **no flag check**:

```rust
let f = match opcode {
    0x13d61f00 => op_secp256k1_verify,
    0x1c3a8f00 => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
return f(allocator, argument_list, max_cost, flags);
``` [3](#0-2) 

The 4-byte opcodes `0x13d61f00` and `0x1c3a8f00` are the original cost-formula-encoded forms of the secp operators, confirmed by the benchmark tool which maps them by name: [4](#0-3) 

The secp operator implementations themselves (`op_secp256k1_verify`, `op_secp256r1_verify`) do not check `ENABLE_SECP_OPS` internally — they accept any `ClvmFlags` value and ignore it: [5](#0-4) 

The broken invariant is: **when `ENABLE_SECP_OPS` is absent, secp verification must not execute**. The 4-byte path breaks this invariant unconditionally.

---

### Impact Explanation

**Consensus divergence.** The `ENABLE_SECP_OPS` flag is a hard-fork activation gate. Before the hard-fork activates, nodes run without this flag. A program using 1-byte opcodes 64/65 is correctly rejected on such nodes. A program using 4-byte opcodes `0x13d61f00`/`0x1c3a8f00` is **accepted and executed** on those same nodes, producing a real secp verification result instead of the expected unknown-operator no-op or rejection.

This means two nodes with identical flag configurations will disagree on the validity of a transaction depending solely on which opcode encoding the attacker chose. The result is a chain split or mempool inconsistency exploitable by any party who can submit CLVM programs.

The `ENABLE_SECP_OPS` flag is exposed to Python callers via the wheel API: [6](#0-5) 

Any Python-layer caller that sets flags without `ENABLE_SECP_OPS` (including `MEMPOOL_MODE`, which does not include it) is silently exposed to the bypass. [7](#0-6) 

---

### Likelihood Explanation

The 4-byte opcode encoding is not obscure — it is the original encoding used before 1-byte aliases were assigned, and it is used directly in the project's own benchmark tooling. Any attacker who reads the source or the benchmark binary can discover the 4-byte opcodes. No privileged access is required: the attacker only needs to submit a CLVM program with a 4-byte operator atom set to `0x13d61f00` or `0x1c3a8f00`. This is fully attacker-controlled via serialized CLVM bytes passed to `run_program`.

---

### Recommendation

Add the `ENABLE_SECP_OPS` flag check to the 4-byte opcode dispatch path, mirroring the 1-byte path:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

Add test cases that verify both 4-byte secp opcodes are rejected (treated as unknown operators) when `ENABLE_SECP_OPS` is absent, analogous to the existing tests for 1-byte opcodes 64 and 65: [8](#0-7) 

---

### Proof of Concept

Construct a CLVM program whose operator atom is the 4-byte atom `\x13\xd6\x1f\x00` (secp256k1_verify) with valid pubkey/msg/sig arguments. Run it via `run_program` with `ChiaDialect::new(ClvmFlags::empty())` (no `ENABLE_SECP_OPS`). The program executes and returns `nil` (success) with cost 1,300,000 — identical to running with `ENABLE_SECP_OPS` set. Run the same verification using 1-byte opcode 64 without `ENABLE_SECP_OPS | NO_UNKNOWN_OPS`: it falls through to `unknown_operator` and either returns a no-op cost or raises `Unimplemented`. The two encodings of the same operator produce divergent outcomes under the same flag configuration, confirming the bypass. [9](#0-8)

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

**File:** src/chia_dialect.rs (L157-182)
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
```

**File:** src/chia_dialect.rs (L248-249)
```rust
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

**File:** tools/src/bin/benchmark-clvm-cost.rs (L830-845)
```rust
            opcode: 0x13d61f00,
            name: "secp256k1_verify",
            arg: Placeholder::ThreeArgs(Some(k1_pk), Some(k1_msg), Some(k1_sig)),
            arg_scale: 1,
            root: 1,
            extra: None,
            flags: ALLOW_FAILURE,
        },
        Operator {
            opcode: 0x1c3a8f00,
            name: "secp256r1_verify",
            arg: Placeholder::ThreeArgs(Some(r1_pk), Some(r1_msg), Some(r1_sig)),
            arg_scale: 1,
            root: 1,
            extra: None,
            flags: ALLOW_FAILURE,
```

**File:** src/secp_ops.rs (L61-68)
```rust
pub fn op_secp256k1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let cost = SECP256K1_VERIFY_COST;
    check_cost(cost, max_cost)?;
```

**File:** wheel/src/api.rs (L321-321)
```rust
    m.add("ENABLE_SECP_OPS", ClvmFlags::ENABLE_SECP_OPS.bits())?;
```

**File:** src/run_program.rs (L1365-1382)
```rust
        // Opcode 64 without ENABLE_SECP_OPS is unimplemented
        RunProgramTest {
            prg: "(secp256k1_verify_64 (q . 0x02888b0c110ef0b4962e3fc6929cbba7a8bb25b4b2c885f55c76365018c909b439) (q . 0x74c2941eb2ebe5aa4f2287a4c5e506a6290c045004058de97a7edf0122548668) (q . 0x1acb7a6e062e78ccd4237b12c22f02b5a8d9b33cb3ba13c35e88e036baa1cbca75253bb9a96ffc48b43196c69c2972d8f965b1baa4e52348d8081cde65e6c018))",
            args: "()",
            flags: ClvmFlags::NO_UNKNOWN_OPS,
            result: None,
            cost: 0,
            err: "unimplemented operator",
        },
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
