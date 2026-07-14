### Title
Unchecked u64 Multiplication Overflow in `op_unknown` Cost Computation Allows Cost Undercharge — (File: `src/more_ops.rs`)

---

### Summary

`op_unknown` in `src/more_ops.rs` performs an unchecked `u64 *= u64` multiplication at line 261. In Rust release mode, integer overflow wraps silently. When the wrapped result is ≤ `u32::MAX`, the function returns `Ok` with a drastically undercharged cost — including zero — allowing attacker-crafted CLVM programs to bypass the block cost limit.

---

### Finding Description

`op_unknown` handles unknown opcodes in lenient (consensus) mode. It extracts a `cost_multiplier` (up to `u32::MAX = 4,294,967,295`) from the opcode bytes, computes a base `cost` from the arguments, then multiplies:

```rust
// src/more_ops.rs lines 260-266
check_cost(cost, max_cost)?;
cost *= cost_multiplier + 1;
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
``` [1](#0-0) 

The `check_cost` call at line 260 ensures `cost ≤ max_cost` **before** the multiplication. After the multiplication, only the `> u32::MAX` guard is applied. There is no overflow check on the multiplication itself.

In Rust release mode, `u64 *= u64` wraps on overflow (defined behavior, no panic). If `cost * (cost_multiplier + 1)` exceeds `u64::MAX`, the result wraps modulo `2^64`. If the wrapped value is `≤ u32::MAX`, the guard passes and the function returns `Ok(Reduction(wrapped_cost, nil))` — with a cost that is orders of magnitude lower than the true cost.

The `cost_multiplier` is decoded from attacker-controlled opcode bytes:

```rust
// src/more_ops.rs lines 201-207
let cost_function = (op[op.len() - 1] & 0b11000000) >> 6;
let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
    Some(v) => v as u64,
    None => { return Err(EvalErr::Invalid(o))?; }
};
``` [2](#0-1) 

The base `cost` is computed from attacker-controlled argument lengths (cost functions 1–3). Both inputs to the multiplication are fully attacker-controlled.

---

### Impact Explanation

Chia's block cost limit is ~11 billion (`11 × 10⁹`), which exceeds `u32::MAX = 4,294,967,295`. This means `max_cost > u32::MAX`, so `cost` can legally reach values above `u32::MAX` before the multiplication.

**Concrete overflow scenario:**

- `cost_multiplier = u32::MAX = 4,294,967,295` → `cost_multiplier + 1 = 2^32`
- `cost = 2^33 = 8,589,934,592` (achievable with cost_function=1, see PoC)
- `cost * 2^32 = 2^65` → wraps to `0` in u64
- `0 > u32::MAX` is false → returns `Ok(Reduction(0, nil))`

The operation that should cost `2^65` units is reported as costing **0**. This cost is added to the running total in `run_program`:

```rust
// src/run_program.rs lines 522-523
cost += match op {
    Operation::Apply => self.apply_op(cost, effective_max_cost - cost)?,
``` [3](#0-2) 

A program can include many such unknown ops, each costing 0, without consuming the cost budget. This allows programs to execute more operations per block than the cost limit intends, undermining the resource-accounting invariant that protects validators from excessive computation.

`Cost` is defined as `u64`:

```rust
// src/cost.rs lines 1-10
pub type Cost = u64;
pub fn check_cost(cost: Cost, max_cost: Cost) -> Result<()> {
    if cost > max_cost { Err(EvalErr::CostExceeded) } else { Ok(()) }
}
``` [4](#0-3) 

The `check_cost` function cannot detect the post-multiplication wrap because it is only called on the pre-multiplication value.

---

### Likelihood Explanation

The attacker entry path is direct: any CLVM program submitted for on-chain validation can include unknown opcodes with attacker-chosen bytes. The opcode bytes determine `cost_multiplier` and `cost_function`; the argument list determines the base `cost`. Both are fully attacker-controlled.

The overflow requires `cost > u32::MAX` before multiplication, which requires `max_cost > u32::MAX`. Chia's block cost limit (~11 billion) satisfies this. Achieving `cost = 2^33` with cost_function=1 requires ~26 million empty-atom arguments (total cost ~8.3 billion, within the 11 billion limit), which is large but not impossible given the cost model. Smaller multiples of `2^32` (e.g., `3 × 2^32`, `4 × 2^32`) are also valid targets and may be reachable with fewer arguments.

The vulnerability is only reachable in consensus (lenient) mode, not mempool mode (`NO_UNKNOWN_OPS` rejects unknown ops):

```rust
// src/chia_dialect.rs lines 85-89
if flags.contains(ClvmFlags::NO_UNKNOWN_OPS) {
    Err(EvalErr::Unimplemented(o))?
} else {
    op_unknown(allocator, o, args, max_cost)
}
``` [5](#0-4) 

---

### Recommendation

Replace the unchecked multiplication with a checked variant that returns an error on overflow:

```rust
// src/more_ops.rs line 261 — replace:
cost *= cost_multiplier + 1;
// with:
cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
```

This ensures that any multiplication that would overflow is treated as an invalid opcode rather than silently wrapping to a small value.

---

### Proof of Concept

**Target**: `op_unknown` in `src/more_ops.rs`, line 261.

**Opcode construction**:
- Last byte: `0b00xxxxxx` → `cost_function = 0` (constant, base cost = 1) — but this alone cannot overflow (see below for cost_function=1)
- Bytes `[0..len-1]`: encode `u32::MAX = 0xFFFFFFFF` → `cost_multiplier = 4,294,967,295`

**For cost_function=1 (ARITH-like, lines 211–222):**

```
cost = ARITH_BASE_COST + n * ARITH_COST_PER_ARG + bytes_total * ARITH_COST_PER_BYTE
     = 99 + n * 320 + bytes_total * 3
``` [6](#0-5) 

Set `n = 26,843,545` (empty-atom arguments) and `bytes_total = 31`:
- `cost = 99 + 26,843,545 × 320 + 31 × 3 = 99 + 8,589,934,400 + 93 = 8,589,934,592 = 2^33`

Then at line 261:
```
cost *= cost_multiplier + 1
     = 2^33 * 2^32
     = 2^65
     → wraps to 0 (mod 2^64)
```

`0 > u32::MAX` is false → `op_unknown` returns `Ok(Reduction(0, nil))`.

The operation that should cost `2^65` units is accepted with cost **0**, bypassing the block cost limit.

### Citations

**File:** src/more_ops.rs (L201-207)
```rust
    let cost_function = (op[op.len() - 1] & 0b11000000) >> 6;
    let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
        Some(v) => v as u64,
        None => {
            return Err(EvalErr::Invalid(o))?;
        }
    };
```

**File:** src/more_ops.rs (L211-222)
```rust
        1 => {
            let mut cost = ARITH_BASE_COST;
            let mut byte_count: u64 = 0;
            while let Some((arg, rest)) = allocator.next(args) {
                args = rest;
                cost += ARITH_COST_PER_ARG;
                let len = atom_len(allocator, arg, "unknown op")?;
                byte_count += len as u64;
                check_cost(cost + (byte_count as Cost * ARITH_COST_PER_BYTE), max_cost)?;
            }
            cost + (byte_count * ARITH_COST_PER_BYTE)
        }
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

**File:** src/run_program.rs (L522-523)
```rust
            cost += match op {
                Operation::Apply => self.apply_op(cost, effective_max_cost - cost)?,
```

**File:** src/cost.rs (L1-10)
```rust
use crate::error::{EvalErr, Result};

pub type Cost = u64;

pub fn check_cost(cost: Cost, max_cost: Cost) -> Result<()> {
    if cost > max_cost {
        Err(EvalErr::CostExceeded)
    } else {
        Ok(())
    }
```

**File:** src/chia_dialect.rs (L85-89)
```rust
    if flags.contains(ClvmFlags::NO_UNKNOWN_OPS) {
        Err(EvalErr::Unimplemented(o))?
    } else {
        op_unknown(allocator, o, args, max_cost)
    }
```
