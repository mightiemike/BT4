### Title
Unchecked `u64` Multiplication in `op_unknown` Silently Wraps, Bypassing Cost Limit — (`File: src/more_ops.rs`)

---

### Summary

`op_unknown` in `src/more_ops.rs` applies a cost multiplier to the computed base cost using a plain Rust `*=` on `u64`. In release builds, Rust integer overflow wraps silently. The multiplication occurs **after** the `check_cost` guard but **before** the `cost > u32::MAX` rejection check. When the product overflows `u64`, it wraps to a small value that passes the rejection check, causing the function to return `Ok` with a near-zero or zero cost. An attacker who controls the CLVM opcode bytes can engineer this wrap, executing unknown opcodes for a fraction of their intended cost and bypassing the program cost limit.

---

### Finding Description

In `op_unknown`, the cost for an unknown opcode is computed in two stages:

1. A base cost is accumulated from the argument list (lines 209–256), bounded by `check_cost(cost, max_cost)` at line 260.
2. The base cost is then scaled by the opcode's embedded multiplier at **line 261**:

```rust
cost *= cost_multiplier + 1;
```

`cost_multiplier` is decoded from the opcode prefix bytes via `u32_from_u8`, so its maximum value is `u32::MAX = 4,294,967,295`, making `cost_multiplier + 1` at most `4,294,967,296`.

The `check_cost` at line 260 ensures `cost ≤ max_cost` before the multiplication. The Chia blockchain's program cost budget is approximately 11 billion (`11 × 10⁹`). Therefore:

```
max product ≈ 11 × 10⁹ × 4.3 × 10⁹ ≈ 47.3 × 10¹⁸ > u64::MAX (≈ 18.4 × 10¹⁸)
```

Overflow is reachable. In Rust release builds, the result wraps modulo `2⁶⁴`. The post-multiplication check at line 262 is:

```rust
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

If the wrapped value is `≤ u32::MAX`, the function returns `Ok` with the wrapped (tiny or zero) cost. The caller in `run_program` adds this cost to the running total, so the attacker's opcode is effectively free.

**Concrete example**:

| Parameter | Value |
|---|---|
| `cost` before multiplication | `2³³ = 8,589,934,592` |
| `cost_multiplier + 1` | `2³¹ = 2,147,483,648` |
| Product | `2⁶⁴ ≡ 0 (mod 2⁶⁴)` |
| Post-wrap cost | `0` |
| Check `0 > u32::MAX`? | **False** → returns `Ok(Reduction(0, nil))` |

`2³³ ≈ 8.6 × 10⁹ < 11 × 10⁹` (Chia max cost), so `cost` can legitimately reach this value before the multiplication.

The opcode bytes to trigger this:
- Prefix `[0x7f, 0xff, 0xff, 0xff]` → `cost_multiplier = 2,147,483,647`, so `cost_multiplier + 1 = 2³¹`
- Last byte with bits 7–6 = `01` → `cost_function = 1` (add-like cost)
- Enough large atom arguments to drive base cost to `2³³`

---

### Impact Explanation

The cost model is the primary DoS defense in CLVM. Every program must stay within a `max_cost` budget; programs that exceed it are rejected. By engineering an overflow in `op_unknown`, an attacker can include unknown opcodes that should consume a large fraction of the budget but instead consume zero cost. This allows programs that should be rejected as too expensive to pass the cost limit. Repeated use across many transactions or blocks constitutes a resource-exhaustion attack against Chia full nodes, since the nodes execute work without the attacker paying the corresponding cost.

---

### Likelihood Explanation

The attacker fully controls the CLVM opcode bytes submitted to the network. The opcode encoding is public and documented in the `op_unknown` cost formula. Constructing the exact byte sequence to trigger the overflow requires only knowledge of the cost formula and arithmetic — no privileged access, no social engineering, and no dependency on external state. The Chia max cost budget (`~11 × 10⁹`) is large enough to make the overflow reachable. The attack is therefore straightforward for any party who can submit CLVM programs to the network.

---

### Recommendation

Replace the plain multiplication with a checked or saturating variant. If the product overflows, treat it as an invalid opcode:

```rust
// Before (line 261):
cost *= cost_multiplier + 1;

// After:
cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
```

This ensures that any product exceeding `u64::MAX` is immediately rejected rather than silently wrapping to a small value that bypasses the subsequent `u32::MAX` guard.

---

### Proof of Concept

```
// Opcode bytes: [0x7f, 0xff, 0xff, 0xff, 0x40]
//   prefix [0x7f,0xff,0xff,0xff] → cost_multiplier = 2^31 - 1
//   last byte 0x40 → cost_function = 1 (bits 7-6 = 01)
//
// cost_multiplier + 1 = 2^31
//
// Supply enough large atom arguments so that:
//   cost = ARITH_BASE_COST + n*ARITH_COST_PER_ARG + bytes*ARITH_COST_PER_BYTE = 2^33
//
// check_cost(2^33, max_cost=11e9) passes (2^33 ≈ 8.6e9 < 11e9)
//
// cost *= 2^31  →  2^33 * 2^31 = 2^64 ≡ 0 (mod 2^64)
//
// if 0 > u32::MAX  →  false
// → Ok(Reduction(0, nil))   // zero cost charged
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** src/more_ops.rs (L160-207)
```rust
pub fn op_unknown(
    allocator: &mut Allocator,
    o: NodePtr,
    mut args: NodePtr,
    max_cost: Cost,
) -> Response {
    // unknown opcode in lenient mode
    // unknown ops are reserved if they start with 0xffff
    // otherwise, unknown ops are no-ops, but they have costs. The cost is computed
    // like this:

    // byte index (reverse):
    // | 4 | 3 | 2 | 1 | 0          |
    // +---+---+---+---+------------+
    // | multiplier    |XX | XXXXXX |
    // +---+---+---+---+---+--------+
    //  ^               ^    ^
    //  |               |    + 6 bits ignored when computing cost
    // cost_multiplier  |
    // (up to 4 bytes)  + 2 bits
    //                    cost_function

    // 1 is always added to the multiplier before using it to multiply the cost, this
    // is since cost may not be 0.

    // cost_function is 2 bits and defines how cost is computed based on arguments:
    // 0: constant, cost is 1 * (multiplier + 1)
    // 1: computed like operator add, multiplied by (multiplier + 1)
    // 2: computed like operator mul, multiplied by (multiplier + 1)
    // 3: computed like operator concat, multiplied by (multiplier + 1)

    // this means that unknown ops where cost_function is 1, 2, or 3, may still be
    // fatal errors if the arguments passed are not atoms.

    let op_atom = allocator.atom(o);
    let op = op_atom.as_ref();

    if op.is_empty() || (op.len() >= 2 && op[0] == 0xff && op[1] == 0xff) {
        Err(EvalErr::Reserved(o))?;
    }

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
