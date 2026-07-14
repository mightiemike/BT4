### Title
Incorrect 32-bit Cost Cap in `op_unknown` Rejects Valid High-Cost Unknown Opcodes — (File: `src/more_ops.rs`)

---

### Summary

In `op_unknown` (`src/more_ops.rs`, line 262), after multiplying the computed base cost by `(cost_multiplier + 1)`, the result is checked against `u32::MAX as u64` (≈ 4.29 billion). However, `Cost` is defined as `u64` (max ≈ 18.4 × 10¹⁸). Any unknown opcode whose scaled cost falls between `u32::MAX + 1` and the caller's `max_cost` is incorrectly rejected with `EvalErr::Invalid` instead of being accepted with its computed cost. This is a direct analog of the MAX_UINT bug: a 32-bit ceiling is applied where a 64-bit ceiling is correct.

---

### Finding Description

`op_unknown` handles unknown opcodes in lenient mode. It computes a base cost (bounded by `max_cost` via `check_cost`), then scales it:

```rust
// src/more_ops.rs lines 258–266
assert!(cost > 0);

check_cost(cost, max_cost)?;
cost *= cost_multiplier + 1;
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
``` [1](#0-0) 

`Cost` is `u64`: [2](#0-1) 

`cost_multiplier` is derived from `u32_from_u8(...)` cast to `u64`, so it is at most `u32::MAX = 0xFFFF_FFFF`: [3](#0-2) 

With `cost_multiplier = u32::MAX` and base cost = 1 (cost_function 0), the scaled cost is `0x1_0000_0000 = 4,294,967,296`, which exceeds `u32::MAX` by exactly 1. The check fires and returns `EvalErr::Invalid`. Yet Chia's block `max_cost` is 11,000,000,000 — well above `u32::MAX` — so this cost is within budget and the program should succeed.

The root cause is identical in structure to the reported MAX_UINT bug: a narrower integer type's maximum (`u32::MAX`) is used as the sentinel for a wider type (`u64 / Cost`), silently truncating the valid range.

---

### Impact Explanation

**Consensus divergence.** Programs using unknown opcodes (reachable via softfork guards in `ChiaDialect`) whose scaled cost falls in the range `(u32::MAX, max_cost]` are rejected with `EvalErr::Invalid` instead of returning a valid `Reduction`. Any implementation or future version that corrects this limit to `u64::MAX` (or uses `checked_mul` + `check_cost`) would accept these programs, producing a split in which nodes disagree on program validity. The error variant is also semantically wrong: `EvalErr::Invalid` signals a malformed opcode, not a cost overrun, so callers cannot distinguish the two failure modes.

---

### Likelihood Explanation

**Medium.** Unknown opcodes are actively used in softfork guards on Chia mainnet. A developer or attacker can craft an opcode byte string whose `cost_multiplier` field (up to 4 bytes, unsigned) is large enough that `base_cost × (cost_multiplier + 1) > u32::MAX` while remaining within the block's `max_cost`. The trigger requires no special privileges — only attacker-controlled CLVM bytes passed to `run_program`.

---

### Recommendation

Replace the post-multiplication check with overflow-safe arithmetic and a proper cost check:

```rust
let cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::CostExceeded)?;
check_cost(cost, max_cost)?;
Ok(Reduction(cost, allocator.nil()))
```

This aligns the limit with the actual `Cost = u64` type, matches the semantics of every other operator (which use `check_cost` / `CostExceeded`), and eliminates the incorrect `u32::MAX` sentinel.

---

### Proof of Concept

Construct an unknown opcode atom with:
- `cost_function` bits = `0b00` (constant cost = 1)
- `cost_multiplier` bytes = `[0xFF, 0xFF, 0xFF, 0xFF]` → `u32::MAX = 4,294,967,295`
- Last byte low 6 bits = `0x00`

Opcode bytes: `[0xFF, 0xFF, 0xFF, 0xFF, 0x00]`

Scaled cost = `1 × (4,294,967,295 + 1) = 4,294,967,296 = 0x1_0000_0000`.

With `max_cost = 11_000_000_000`:

```
check_cost(1, 11_000_000_000)  → Ok   (base cost within budget)
cost = 1 * 4_294_967_296 = 4_294_967_296
4_294_967_296 > u32::MAX (4_294_967_295) → true
→ Err(EvalErr::Invalid)        ← WRONG; should be Ok(Reduction(4_294_967_296, nil))
```

The program is rejected despite its cost being within the caller-supplied budget, demonstrating the incorrect 32-bit ceiling applied to a 64-bit `Cost` type. [1](#0-0) [4](#0-3)

### Citations

**File:** src/more_ops.rs (L202-207)
```rust
    let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
        Some(v) => v as u64,
        None => {
            return Err(EvalErr::Invalid(o))?;
        }
    };
```

**File:** src/more_ops.rs (L258-266)
```rust
    assert!(cost > 0);

    check_cost(cost, max_cost)?;
    cost *= cost_multiplier + 1;
    if cost > u32::MAX as u64 {
        Err(EvalErr::Invalid(o))?
    } else {
        Ok(Reduction(cost as Cost, allocator.nil()))
    }
```

**File:** src/cost.rs (L1-11)
```rust
use crate::error::{EvalErr, Result};

pub type Cost = u64;

pub fn check_cost(cost: Cost, max_cost: Cost) -> Result<()> {
    if cost > max_cost {
        Err(EvalErr::CostExceeded)
    } else {
        Ok(())
    }
}
```
