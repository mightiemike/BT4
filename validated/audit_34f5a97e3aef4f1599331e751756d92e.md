### Title
`DISABLE_OP` Flag Applied Too Broadly Completely Disables `op_modpow` in Mempool Mode, Causing Consensus/Mempool Divergence — (File: src/chia_dialect.rs)

---

### Summary

The `DISABLE_OP` flag, when set as part of `MEMPOOL_MODE`, completely disables opcode 60 (`op_modpow`) by returning `EvalErr::Unimplemented`. This is inconsistent with how the same flag is applied to the analogous arithmetic operators `op_div`, `op_divmod`, and `op_mod`, where `DISABLE_OP` only adds an extra input-size restriction rather than eliminating the operator entirely. The result is a concrete consensus/mempool divergence: a CLVM program using `modpow` with valid inputs is rejected by every mempool node but accepted by every consensus-mode node.

---

### Finding Description

In `src/chia_dialect.rs`, the operator dispatch table for single-byte opcodes handles opcode 60 as follows:

```rust
60 => {
    if flags.contains(ClvmFlags::DISABLE_OP) {
        return Err(EvalErr::Unimplemented(o))?;
    }
    op_modpow
}
``` [1](#0-0) 

`DISABLE_OP` is a constituent of `MEMPOOL_MODE`:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
``` [2](#0-1) 

Therefore, every mempool node running `MEMPOOL_MODE` will return `Unimplemented` for any program that invokes opcode 60, regardless of the size or validity of its arguments.

By contrast, the three other arithmetic operators that also inspect `DISABLE_OP` — `op_div` (opcode 19), `op_divmod` (opcode 20), and `op_mod` (opcode 61) — only use the flag to enforce an *additional* size restriction on the first operand (`a0_len > 2048`). They remain fully functional for normal-sized inputs:

```rust
// op_div (opcode 19)
if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
``` [3](#0-2) 

```rust
// op_mod (opcode 61)
if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
    return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
}
``` [4](#0-3) 

`op_modpow` receives no such conditional treatment — the flag unconditionally kills the operator. Opcode 61 (`op_mod`) is dispatched without any gate at all:

```rust
61 => op_mod,
``` [5](#0-4) 

`op_modpow` is a hardforked-in operator sitting in the main dispatch table alongside the BLS operators (opcodes 48–59) and `op_mod` (opcode 61). It has its own well-defined cost model and its own 256-byte operand size limit enforced inside the function itself:

```rust
if bsize > 256 || esize > 256 || msize > 256 {
    return Err(EvalErr::InvalidOpArg(input, "modpow".to_string()));
}
``` [6](#0-5) 

There is no documentation comment on `DISABLE_OP` explaining why `modpow` should be treated differently from `mod`, `div`, and `divmod`.

---

### Impact Explanation

**Concrete corrupted result**: A CLVM program invoking `modpow` with valid inputs (operands ≤ 256 bytes) produces two different outcomes depending on the flag set used:

| Validation context | Flags | Result for `(modpow (q . 12345) (q . 6789) (q . 44444444444))` |
|---|---|---|
| Mempool node | `MEMPOOL_MODE` (includes `DISABLE_OP`) | `EvalErr::Unimplemented` — **rejected** |
| Consensus node | `ClvmFlags::empty()` | `Reduction(18241, 13456191581)` — **accepted** |

This is a direct consensus/mempool divergence. A farmer/miner who bypasses the mempool and includes a `modpow`-using spend directly in a block will have that block accepted by all consensus-mode peers, while mempool-mode peers would have rejected the same spend. Legitimate users who craft puzzles relying on `modpow` find their transactions permanently stuck: the mempool rejects them unconditionally, so they can never propagate through the peer-to-peer layer under normal operation.

---

### Likelihood Explanation

**High** for the operational impact (legitimate `modpow` use is silently broken in all deployed mempool nodes). **Medium** for active exploitation: a miner who controls block production can include `modpow` spends that bypass mempool filtering, but this requires block-production capability. The attacker-controlled entry path is simply crafting CLVM bytes that invoke opcode 60 with any valid arguments — no special privileges are needed to trigger the divergence itself, only to exploit it for block-level manipulation.

---

### Recommendation

Apply `DISABLE_OP` to `op_modpow` consistently with the other arithmetic operators: replace the unconditional `Unimplemented` return with a size-based restriction analogous to the `a0_len > 2048` check used in `op_div`, `op_divmod`, and `op_mod`. If `modpow` is intended to be fully unavailable in mempool mode, it should be removed from the hardforked main dispatch table and placed behind a softfork guard, and the inconsistency with `op_mod` must be resolved. Either way, the current state — where `op_mod` (opcode 61) is always available but `op_modpow` (opcode 60) is silently killed by the same flag — is an undocumented, untested divergence.

---

### Proof of Concept

```
Program:  (modpow (q . 12345) (q . 6789) (q . 44444444444))
Args:     ()

Run with ClvmFlags::empty()  (consensus mode):
  → Ok(Reduction(18241, 13456191581))   ✓ accepted

Run with MEMPOOL_MODE (includes DISABLE_OP):
  → Err(Unimplemented(op=60))           ✗ rejected
```

The test infrastructure in `src/run_program.rs` already confirms the consensus-mode result:

```rust
#[case::modpow(
    "(i (= (modpow (q . 12345) (q . 6789) (q . 44444444444)) (q . 13456191581)) (q . 0) (q x))",
    (18241, 0, ClvmFlags::empty()),
    ""
)]
``` [7](#0-6) 

No test exercises `op_modpow` under `MEMPOOL_MODE` or `DISABLE_OP`, leaving the divergence undetected by the existing test suite. [8](#0-7)

### Citations

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L239-244)
```rust
            60 => {
                if flags.contains(ClvmFlags::DISABLE_OP) {
                    return Err(EvalErr::Unimplemented(o))?;
                }
                op_modpow
            }
```

**File:** src/chia_dialect.rs (L245-245)
```rust
            61 => op_mod,
```

**File:** src/more_ops.rs (L665-667)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
```

**File:** src/more_ops.rs (L769-771)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
    }
```

**File:** src/more_ops.rs (L1266-1268)
```rust
    if bsize > 256 || esize > 256 || msize > 256 {
        return Err(EvalErr::InvalidOpArg(input, "modpow".to_string()));
    }
```

**File:** src/more_ops.rs (L1462-1477)
```rust
    // only op_div, op_divmod, op_mod, and op_modpow inspect flags.
    // test those separately to avoid running all the flag-insensitive
    // operators multiple times.
    #[test]
    #[ignore = "slow: run with `cargo test -- --include-ignored`"]
    fn test_large_operand_with_flags() {
        type Op = fn(&mut Allocator, NodePtr, Cost, ClvmFlags) -> Response;
        #[allow(clippy::type_complexity)]
        let cases: &[(&str, Op, u32, u32, ClvmFlags, Option<EvalErr>)] = &[
            ("div", op_div, 8, 2, ClvmFlags::DISABLE_OP, None),
            ("div", op_div, 8, 2, ClvmFlags::MALACHITE, None),
            ("divmod", op_divmod, 8, 2, ClvmFlags::DISABLE_OP, None),
            ("divmod", op_divmod, 8, 2, ClvmFlags::MALACHITE, None),
            ("modulus", op_mod, 8, 2, ClvmFlags::DISABLE_OP, None),
            ("modulus", op_mod, 8, 2, ClvmFlags::MALACHITE, None),
            ("modpow", op_modpow, 8, 3, ClvmFlags::MALACHITE, None),
```

**File:** src/run_program.rs (L1471-1475)
```rust
    #[case::modpow(
        "(i (= (modpow (q . 12345) (q . 6789) (q . 44444444444)) (q . 13456191581)) (q . 0) (q x))",
        (18241, 0, ClvmFlags::empty()),
        ""
    )]
```
