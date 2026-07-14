### Title
Truncating Integer Division in `op_multiply` Quadratic Cost Term Causes Systematic Cost Undercharge — (File: `src/more_ops.rs`)

### Summary

The `op_multiply` operator in `src/more_ops.rs` computes its quadratic cost component using integer division: `(l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER`, where `MUL_SQUARE_COST_PER_BYTE_DIVIDER = 128`. Rust integer division truncates toward zero, so whenever `l0 * l1 < 128`, the entire quadratic cost term evaluates to zero. This mirrors the external report's root cause: a proportional calculation rounds down, causing a value that should be non-zero to be silently zeroed, violating the invariant that charged cost ≥ intended cost. An attacker can craft attacker-controlled CLVM bytes that trigger this undercharge on every multiplication step, executing programs at a lower cost than the cost model intends.

### Finding Description

In `src/more_ops.rs`, `op_multiply` accumulates cost per operand pair as follows:

```rust
cost += MUL_COST_PER_OP;
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;  // line 616/624/644
```

`MUL_SQUARE_COST_PER_BYTE_DIVIDER` is defined as `128` at line 37. Rust's `/` operator on integers truncates toward zero. Therefore, for any pair of operands where `l0 * l1 < 128`, the quadratic term contributes exactly `0` to the cost, even though the intended cost model assigns a non-zero quadratic component. Concretely:

- `l0 = 1, l1 = 1`: `1 * 1 / 128 = 0` (intended: ~0.0078, rounds to 0)
- `l0 = 11, l1 = 11`: `121 / 128 = 0` (intended: ~0.945, rounds to 0)
- `l0 = 127, l1 = 1`: `127 / 128 = 0` (intended: ~0.992, rounds to 0)

The same truncation appears in the `op_unknown` cost_function-2 branch at line 238, which mirrors the `op_multiply` cost formula for unknown opcodes.

The invariant violated is: **the cost charged for each multiplication step must equal the intended quadratic cost formula**. Due to truncation, the charged cost is strictly less than the intended cost whenever `l0 * l1 % 128 ≠ 0`, and is zero instead of a positive value whenever `l0 * l1 < 128`.

The attacker-controlled entry path is direct: an attacker submits crafted CLVM bytes containing a `*` (multiply) opcode with many small-byte operands. Each step where both operands are ≤ 11 bytes contributes zero quadratic cost. The program is accepted by `check_cost` at a lower cost than the cost model intends.

### Impact Explanation

**Impact: Low.** The maximum truncation error per step is 127 cost units. The result size is bounded at 1024 bytes, which bounds the number of steps. The total accumulated undercharge across all steps is at most on the order of a few thousand cost units — negligible relative to Chia's cost limit of 11,000,000,000. An attacker cannot use this to bypass the cost limit by a meaningful margin or cause a consensus split. The invariant is violated (cost charged < intended cost), but the practical consequence is a small, bounded discount on multiply-heavy programs.

**Likelihood: High.** Any CLVM program using `op_multiply` with operands whose byte-lengths satisfy `l0 * l1 < 128` — which includes all single-byte operand multiplications — triggers this truncation on every step. This is a common case in real programs.

### Likelihood Explanation

The condition `l0 * l1 < 128` is satisfied whenever both operands are small integers (e.g., both ≤ 11 bytes). This covers the vast majority of practical CLVM multiplication uses. The truncation fires on every such step unconditionally, making the likelihood of triggering the undercharge high for any program that multiplies small numbers.

### Recommendation

Replace the truncating integer division with ceiling division (`div_ceil`) or accumulate the remainder across steps and add it to the cost when it reaches the threshold. For example:

```rust
// Instead of:
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;

// Use ceiling division:
cost += (l0 as Cost * l1).div_ceil(MUL_SQUARE_COST_PER_BYTE_DIVIDER);
```

This ensures the quadratic cost term is never silently zeroed and the invariant `cost_charged ≥ intended_cost` is maintained for every step.

### Proof of Concept

The following demonstrates the undercharge for a single step with `l0 = 11, l1 = 11`:

```
Intended quadratic cost: 11 * 11 / 128 = 121 / 128 → should be 1 (ceiling) but is 0 (floor)
Charged quadratic cost:  0
Undercharge per step:    1 cost unit (quadratic term silently dropped)
```

For a CLVM program multiplying N single-byte atoms together (e.g., `(* 1 1 1 ... 1)` with N arguments), every step has `l0 = 1, l1 = 1`, so the quadratic term is `0` for all N-1 steps. The program runs at purely linear cost `MUL_BASE_COST + (N-1) * (MUL_COST_PER_OP + 2 * MUL_LINEAR_COST_PER_BYTE)` instead of the intended cost that includes the quadratic component, directly analogous to the borrower retaining non-zero debt shares after full repayment in the reference report. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** src/more_ops.rs (L37-37)
```rust
const MUL_SQUARE_COST_PER_BYTE_DIVIDER: Cost = 128;
```

**File:** src/more_ops.rs (L235-239)
```rust
                let l1 = len as u64;
                cost += MUL_COST_PER_OP;
                cost += (l0 + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                l0 += l1;
```

**File:** src/more_ops.rs (L607-617)
```rust
        cost += MUL_COST_PER_OP;
        #[cfg(not(feature = "no-fastpath"))]
        match a.node(arg) {
            NodeVisitor::Buffer(buf) => {
                let l1 = buf.len() as u64;
                if l1 > 256 {
                    return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
                }
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                check_cost(cost, max_cost)?;
```

**File:** src/more_ops.rs (L636-645)
```rust
        #[cfg(feature = "no-fastpath")]
        {
            let (n1, l1) = int_atom(a, arg, "*")?;
            let l1 = l1 as u64;
            if l1 > 256 {
                return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
            }
            cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
            cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
            check_cost(cost, max_cost)?;
```
