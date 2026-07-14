### Title
Integer Division Truncation Silently Zeros the Quadratic Cost Term in `op_multiply` and `op_unknown` — (File: `src/more_ops.rs`)

---

### Summary

In `src/more_ops.rs`, the quadratic component of the multiplication cost formula uses integer division with a divisor of 128. When the product of the two operand byte-lengths is less than 128, Rust's integer division truncates the result to **0**, silently dropping the entire quadratic cost contribution. This is the direct analog of the Solidity `grant.value / 100 == 0` truncation bug: a cost that should be non-zero is evaluated as zero due to integer division rounding.

---

### Finding Description

The cost model for `op_multiply` charges three components per multiplication step:

1. A per-operation base: `MUL_COST_PER_OP = 885`
2. A linear term: `(l0 + l1) * MUL_LINEAR_COST_PER_BYTE` (6 per byte)
3. A quadratic term: `(l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER` [1](#0-0) 

The quadratic term in `op_multiply` (fast path, Buffer branch): [2](#0-1) 

The same formula in the `U32` fast path: [3](#0-2) 

And in the `no-fastpath` slow path: [4](#0-3) 

Because `MUL_SQUARE_COST_PER_BYTE_DIVIDER = 128` and Rust integer division truncates toward zero, whenever `l0 * l1 < 128` the entire quadratic term evaluates to **0**. This condition holds for any pair of operands where both are ≤ 11 bytes (since 11 × 11 = 121 < 128). For example:

| l0 (bytes) | l1 (bytes) | l0 × l1 | Charged quadratic | Correct (real) |
|---|---|---|---|---|
| 1 | 1 | 1 | 0 | ~0.008 |
| 8 | 8 | 64 | 0 | 0.5 |
| 11 | 11 | 121 | **0** | **~0.945** |
| 12 | 11 | 132 | 1 | ~1.03 |

The identical truncation also appears in `op_unknown` for `cost_function = 2` (mul-like unknown ops): [5](#0-4) 

In `op_unknown`, the computed cost is subsequently multiplied by `(cost_multiplier + 1)`: [6](#0-5) 

This means the dropped quadratic contribution is amplified by the multiplier. With two 11-byte arguments and `cost_multiplier = 1 000 000`, the missing quadratic contribution is `⌊121/128⌋ × 1 000 001 = 0` instead of the intended `~945 001` cost units.

---

### Impact Explanation

The quadratic term models the O(n²) complexity of big-integer multiplication. When it is silently zeroed, the CLVM cost meter undercharges programs that multiply small numbers. An attacker can craft a CLVM program consisting of many chained multiplications of ≤ 11-byte operands and execute more computational work than the declared cost budget should permit. In `op_unknown`, the amplification by `cost_multiplier` makes the absolute undercharge larger for unknown-opcode invocations with large multipliers. The broken invariant is: **the sum of all charged cost components must equal the intended cost formula**; here the quadratic component is unconditionally dropped to zero for a well-defined, attacker-reachable input range.

---

### Likelihood Explanation

The trigger is trivially reachable: any attacker-controlled CLVM byte sequence that invokes `op_multiply` (`*`, opcode `0x12`) with operands whose byte-lengths satisfy `l0 × l1 < 128` hits this path. Small integers (1–4 bytes) are the most common operands in real Chia puzzles, so the truncation fires on virtually every multiplication of ordinary values. The `op_unknown` amplification path requires crafting a specific opcode byte pattern, which is also fully attacker-controlled.

---

### Recommendation

Replace the integer division with a ceiling division or accumulate the quadratic remainder across steps, so that the quadratic cost is never silently dropped. For example:

```rust
// ceiling division: charges at least 1 when l0 * l1 > 0
cost += (l0 as Cost * l1 + MUL_SQUARE_COST_PER_BYTE_DIVIDER - 1)
        / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

This mirrors the recommendation in the original report: introduce specific logic to handle rounding so that the cost component is never evaluated as zero when the true value is non-zero.

---

### Proof of Concept

A CLVM program of the form `(* A B C D ...)` where every atom is an 11-byte integer (e.g., `0x0102030405060708090a0b`) will, on every multiplication step, compute:

```
l0 = 11, l1 = 11
quadratic = (11 * 11) / 128 = 121 / 128 = 0   ← truncated
``` [2](#0-1) 

The charged cost per step is `885 + 132 + 0 = 1017` instead of the intended `885 + 132 + 0 = 1017 + ε`. Repeating this across thousands of multiplications (all within the declared cost budget) allows more actual big-integer work than the cost model accounts for. The attacker controls the operand bytes directly via the serialized CLVM program passed to `run_program`.

### Citations

**File:** src/more_ops.rs (L37-37)
```rust
const MUL_SQUARE_COST_PER_BYTE_DIVIDER: Cost = 128;
```

**File:** src/more_ops.rs (L238-238)
```rust
                cost += (l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

**File:** src/more_ops.rs (L261-261)
```rust
    cost *= cost_multiplier + 1;
```

**File:** src/more_ops.rs (L615-616)
```rust
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

**File:** src/more_ops.rs (L623-624)
```rust
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

**File:** src/more_ops.rs (L643-644)
```rust
            cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
            cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```
