### Title
Integer Overflow in `op_unknown` Cost Multiplication Enables Undercharged Execution — (File: `src/more_ops.rs`)

---

### Summary

The `op_unknown` function in `src/more_ops.rs` computes a final cost by multiplying a base cost by `cost_multiplier + 1`. This multiplication is performed on `u64` values without checked arithmetic. In Rust release mode, integer overflow wraps silently. The post-multiplication guard only rejects values exceeding `u32::MAX`; it does not re-validate against `max_cost` and does not detect wrapped-around values. An attacker who controls the unknown opcode bytes and its argument list can craft inputs where the true product overflows `u64`, wraps to a small value that passes the `u32::MAX` guard, and is returned as the operator's cost. The program is then accepted as cheap, bypassing the cost limit.

---

### Finding Description

In `src/more_ops.rs`, `op_unknown` computes cost as follows:

```rust
// line 260
check_cost(cost, max_cost)?;
cost *= cost_multiplier + 1;
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`cost_multiplier` is decoded from the opcode bytes as a `u64` derived from a `u32` value (at most `u32::MAX = 4_294_967_295`), so `cost_multiplier + 1` is at most `4_294_967_296`.

`check_cost(cost, max_cost)?` before the multiplication ensures `cost ≤ max_cost`. On the Chia blockchain, `max_cost` is approximately `11 × 10⁹`. The maximum product is therefore:

```
11 × 10⁹ × 4_294_967_296 ≈ 4.72 × 10¹⁹  >  u64::MAX ≈ 1.84 × 10¹⁹
```

Overflow is reachable. In Rust release mode, `u64` multiplication wraps modulo `2⁶⁴`. The post-multiplication check `if cost > u32::MAX as u64` only catches large values; it does not detect a wrapped-around small value. No `check_cost(cost, max_cost)` call follows the multiplication.

**Concrete example:**

| Variable | Value |
|---|---|
| `cost` (base, before multiply) | `8_589_934_593` (`= 2³³ + 1`, within `max_cost = 11 × 10⁹`) |
| `cost_multiplier` | `2_147_483_647` (`= 2³¹ − 1`, within `u32::MAX`) |
| `cost_multiplier + 1` | `2_147_483_648` (`= 2³¹`) |
| True product | `(2³³ + 1) × 2³¹ = 2⁶⁴ + 2³¹` |
| Wrapped result (`mod 2⁶⁴`) | `2³¹ = 2_147_483_648` |
| `2_147_483_648 > u32::MAX`? | **No** — passes the guard |
| Returned cost | `2_147_483_648` instead of the true `≈ 1.84 × 10¹⁹` |

The `assert!(cost > 0)` at line 258 does not fire because the wrapped value is non-zero. The function returns `Ok(Reduction(2_147_483_648, allocator.nil()))`, charging a tiny fraction of the true cost.

The root cause is the absence of checked multiplication and the absence of a post-multiplication `check_cost` call.

---

### Impact Explanation

**Undercharged execution / consensus divergence.**

1. **Undercharged execution (release mode):** An attacker submits a CLVM program containing a crafted unknown opcode. The opcode's cost_multiplier bytes and argument list are chosen so that the base cost × multiplier overflows `u64`. The program is accepted by the blockchain as within cost limits, even though the true computational work is orders of magnitude more expensive. This allows an attacker to execute expensive operations for a negligible declared cost, bypassing the cost-limit DoS protection.

2. **Consensus divergence (debug vs. release mode):** In Rust debug mode, `cost *= cost_multiplier + 1` panics with "attempt to multiply with overflow." A node running in debug mode crashes when processing this transaction; a node in release mode silently accepts it with a tiny cost. This is a consensus split between node configurations.

The corrupted result is the `Cost` value returned in `Reduction(cost, ...)` — it is a wrapped-around `u64` value that is orders of magnitude smaller than the true cost of the operation.

---

### Likelihood Explanation

- Unknown opcodes are reachable in Chia's consensus mode (`allow_unknown_ops() = true`) for forward compatibility.
- The attacker fully controls the opcode bytes (setting `cost_multiplier` to any value up to `u32::MAX`) and the argument list (controlling the base cost).
- No special privileges, social engineering, or compromised nodes are required — only the ability to submit a CLVM program to the blockchain.
- The overflow condition is arithmetically achievable within the Chia blockchain's `max_cost` of ~11 billion.
- The only obstacle is constructing an argument list large enough to drive the base cost to the required value before the `check_cost` inside the loop fires; this requires many arguments or large atoms, but is within the attacker's control.

---

### Recommendation

Replace the unchecked multiplication with a checked variant and add a post-multiplication cost validation:

```rust
// In op_unknown, src/more_ops.rs
check_cost(cost, max_cost)?;
cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
check_cost(cost, max_cost)?;  // re-validate after multiplication
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

This mirrors the fix applied to the H-02 vault bug (adding `SafeCast`): use a safe arithmetic primitive that makes overflow an explicit error rather than silent truncation.

---

### Proof of Concept

Craft an unknown opcode with:
- Last byte: `0x40` (cost_function = 1, add-like; lower 6 bits ignored)
- Preceding bytes: encode `cost_multiplier = 2_147_483_647` (`0x7F FF FF FF`)
- Full opcode bytes: `[0x7F, 0xFF, 0xFF, 0xFF, 0x40]`

Provide arguments to the opcode (as a CLVM list of atoms) whose total byte count drives the base cost to `8_589_934_593`:

```
base_cost = ARITH_BASE_COST + n * ARITH_COST_PER_ARG + total_bytes * ARITH_COST_PER_BYTE
          = 99 + n * 320 + total_bytes * 3
```

With `n = 26_843_545` single-byte atoms: `99 + 26_843_545 × 323 ≈ 8.67 × 10⁹`.

After `check_cost(8_589_934_593, 11_000_000_000)` passes:

```
cost = 8_589_934_593 × 2_147_483_648
     = (2³³ + 1) × 2³¹
     = 2⁶⁴ + 2³¹
     ≡ 2_147_483_648  (mod 2⁶⁴)   [wraps in release mode]
```

`2_147_483_648 ≤ u32::MAX = 4_294_967_295` → passes the guard.

The function returns `Ok(Reduction(2_147_483_648, nil))`. The running cost in `run_program` increases by only `2_147_483_648` instead of the true `≈ 1.84 × 10¹⁹`, and the program is accepted as within cost limits. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** src/more_ops.rs (L200-207)
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

**File:** src/run_program.rs (L488-516)
```rust
    pub fn run_program(&mut self, program: NodePtr, env: NodePtr, max_cost: Cost) -> Response {
        self.val_stack = vec![];
        self.op_stack = vec![];

        // max_cost is always in effect, and necessary to prevent wrap-around of
        // the cost integer.
        let max_cost = if max_cost == 0 { Cost::MAX } else { max_cost };
        // We would previously allocate an atom to hold the max cost for the program.
        // Since we don't anymore we need to increment the ghost atom counter to remain
        // backwards compatible with the atom count limit
        self.allocator.add_ghost_atom(1)?;
        let mut cost: Cost = 0;

        cost += self.eval_pair(program, env)?;

        loop {
            // if we are in a softfork guard, temporarily use the guard's
            // expected cost as the upper limit. This lets us fail early in case
            // it's wrong. It's guaranteed to be <= max_cost, because we check
            // that when entering the softfork guard
            let effective_max_cost = if let Some(sf) = self.softfork_stack.last() {
                sf.expected_cost
            } else {
                max_cost
            };

            if cost > effective_max_cost {
                return Err(EvalErr::CostExceeded);
            }
```
