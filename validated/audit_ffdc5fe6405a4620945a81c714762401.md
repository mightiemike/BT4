### Title
Unchecked `u64` Multiplication Overflow in `op_unknown` Cost Calculation Causes Cost Undercharging — (File: `src/more_ops.rs`)

---

### Summary

In `op_unknown` (`src/more_ops.rs`, line 261), the statement `cost *= cost_multiplier + 1` is an unchecked `u64 × u64` multiplication. The release profile in `Cargo.toml` does not set `overflow-checks = true`, so Rust uses wrapping (two's-complement) arithmetic in release builds. An attacker who controls both the opcode bytes (which set `cost_multiplier`) and the argument list (which sets the pre-multiplication `cost`) can make the product silently wrap to a value ≤ `u32::MAX`, causing the function to return a falsely small cost — including zero — instead of the correct large value or an error.

---

### Finding Description

`op_unknown` computes the cost of unknown opcodes in two phases:

**Phase 1 — base cost** (lines 209–256): iterates over arguments and accumulates `cost` according to `cost_function` (0–3). A `check_cost(cost, max_cost)` guard inside the loop ensures `cost ≤ max_cost` before the loop exits.

**Phase 2 — multiply** (lines 260–266):

```rust
check_cost(cost, max_cost)?;          // cost ≤ max_cost here
cost *= cost_multiplier + 1;          // ← unchecked u64 multiplication
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`cost_multiplier` is derived from `u32_from_u8(&op[0..op.len()-1])`, so its maximum value is `u32::MAX = 4 294 967 295`, making `cost_multiplier + 1` at most `2^32 = 4 294 967 296`. [1](#0-0) 

The multiplication at line 261 is a plain `u64 *= u64` with no `checked_mul` or saturation. [2](#0-1) 

The release profile only sets `lto = "thin"` and does **not** enable `overflow-checks`, so Rust's default wrapping behaviour applies in production builds. [3](#0-2) 

When the product wraps to a value ≤ `u32::MAX`, the `if cost > u

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

**File:** Cargo.toml (L43-45)
```text
[profile.release]
lto = "thin"

```
