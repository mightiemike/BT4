### Title
Silent u64 Overflow in `op_unknown` Cost Multiplication Bypasses Cost Limit — (`File: src/more_ops.rs`)

### Summary
In `op_unknown` (`src/more_ops.rs`), the final cost multiplication `cost *= cost_multiplier + 1` is a plain Rust `u64` multiplication that wraps silently in release builds. The overflow guard that follows (`if cost > u32::MAX as u64`) only catches the wrapped result, not the true product. An attacker-controlled CLVM program can craft an unknown opcode and argument list that causes the accumulated cost to land on a value whose product with `cost_multiplier + 1` wraps to a value ≤ `u32::MAX`, causing the function to return a near-zero cost for what should be a very expensive operation.

### Finding Description

`op_unknown` computes a base cost from the opcode's argument list (using one of four cost functions), then multiplies by `cost_multiplier + 1` derived from the opcode bytes:

```rust
// src/more_ops.rs lines 258-266
assert!(cost > 0);
check_cost(cost, max_cost)?;
cost *= cost_multiplier + 1;          // ← plain u64 multiply, wraps in release
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`cost_multiplier` is decoded from up to 4 bytes of the opcode, so `cost_multiplier + 1` can be as large as `2^32 = 4294967296`. `cost` before the multiply is bounded only by `max_cost` (which can be up to `u64::MAX` when the caller passes 0, per `run_program.rs` line 494). The product `cost * (cost_multiplier + 1)` can therefore exceed `u64::MAX`.

In Rust release builds, integer overflow wraps modulo `2^64` — there is no panic. The subsequent check `if cost > u32::MAX as u64` tests the *wrapped* value, not the true product. If the wrapped value is ≤ `u32::MAX`, the function returns `Reduction(wrapped_cost, nil)` — a tiny or zero cost — instead of an error.

**Concrete trigger:** Choose `cost_multiplier = 2^32 - 1` (opcode bytes `0xFF FF FF FF <cost_fn_byte>`). The loop for `cost_function = 2` (mul-like) accumulates:

```
cost = MUL_BASE_COST + MUL_COST_PER_OP + (l0 + l1)*6 + (l0*l1)/128
```

With two argument atoms of size ≈ 741 KB each, `cost ≈ 2^32`. Then:

```
cost * 2^32  mod 2^64  =  (cost mod 2^32) * 2^32
```

If `cost` is a multiple of `2^32`, the product wraps to 0. `0 > u32::MAX` is false, so the function returns `Reduction(0, nil)` — **zero cost** for an operation that consumed significant resources.

The cost accumulation inside the loop for `cost_function = 2` also uses plain `u64` arithmetic (`cost += (l0 + l1) * MUL_LINEAR_COST_PER_BYTE` and `cost += (l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER`) without overflow checks, compounding the issue.

### Impact Explanation

An attacker can submit a CLVM spend bundle containing an unknown opcode crafted to trigger the overflow. The node accepts the spend as within the cost limit (because the reported cost is near zero), while the true computational cost is orders of magnitude higher. This enables:

1. **Undercharged execution / cost-limit bypass**: The attacker executes a program whose true cost far exceeds the block cost limit, but the metered cost is near zero. Validators accept the block.
2. **Consensus divergence**: Nodes running in debug mode would panic on the overflow; nodes in release mode would silently wrap. This produces a split between debug and release validators on the same block.

### Likelihood Explanation

The attacker fully controls both the opcode bytes (setting `cost_multiplier` and `cost_function`) and the argument atoms (setting `l0`, `l1`). The required atom sizes (~741 KB each for the quadratic cost path) are well within typical allocator heap limits. The opcode byte pattern to select `cost_function = 2` and `cost_multiplier = 2^32 - 1` is a fixed 5-byte prefix. No special privileges are required; any coin spend can embed an unknown opcode in lenient/consensus mode.

### Recommendation

Replace the plain multiplication with a checked or saturating variant, and reject on overflow:

```rust
// src/more_ops.rs, replacing line 261
let Some(new_cost) = cost.checked_mul(cost_multiplier + 1) else {
    return Err(EvalErr::Invalid(o))?;
};
cost = new_cost;
```

Additionally, audit all `cost +=` and `cost *=` sites in `op_unknown`'s inner loops (lines 237–238) for the same pattern, replacing with `checked_add`/`checked_mul` or bounding `l0`/`l1` before use.

### Proof of Concept

1. Construct a 5-byte opcode: `[0xFF, 0xFF, 0xFF, 0xFF, 0x80]`.
   - Bytes `[0..4]` = `0xFFFFFFFF` → `cost_multiplier = 4294967295`, so `cost_multiplier + 1 = 2^32`.
   - Last byte `0x80`: bits 7-6 = `10` → `cost_function = 2` (mul-like).
2. Attach two argument atoms each of size `L` chosen so that `977 + 12L + L²/128 = 2^32` (approximately `L = 741455` bytes ≈ 724 KB).
3. Submit this as an unknown opcode call in a CLVM program run in consensus (lenient) mode.
4. `op_unknown` accumulates `cost = 2^32`, then computes `cost *= 2^32` → wraps to `0` in u64.
5. `0 > u32::MAX` is false → returns `Reduction(0, nil)`.
6. The caller's cost counter receives `0` for this operation, bypassing the block cost limit. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** src/more_ops.rs (L200-242)
```rust

    let cost_function = (op[op.len() - 1] & 0b11000000) >> 6;
    let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
        Some(v) => v as u64,
        None => {
            return Err(EvalErr::Invalid(o))?;
        }
    };

    let mut cost = match cost_function {
        0 => 1,
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
        2 => {
            let mut cost = MUL_BASE_COST;
            let mut first_iter: bool = true;
            let mut l0: u64 = 0;
            while let Some((arg, rest)) = allocator.next(args) {
                args = rest;
                let len = atom_len(allocator, arg, "unknown op")?;
                if first_iter {
                    l0 = len as u64;
                    first_iter = false;
                    continue;
                }
                let l1 = len as u64;
                cost += MUL_COST_PER_OP;
                cost += (l0 + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                l0 += l1;
                check_cost(cost, max_cost)?;
            }
            cost
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

**File:** src/run_program.rs (L492-494)
```rust
        // max_cost is always in effect, and necessary to prevent wrap-around of
        // the cost integer.
        let max_cost = if max_cost == 0 { Cost::MAX } else { max_cost };
```
