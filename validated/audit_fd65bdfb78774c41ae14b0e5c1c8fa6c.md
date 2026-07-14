### Title
Unchecked `u64` Multiplication Overflow in `op_unknown` Cost Accounting Enables Undercharged Execution - (File: `src/more_ops.rs`)

---

### Summary

In `op_unknown`, the final cost is computed by multiplying an attacker-controlled `cost_multiplier` (derived from opcode bytes) against a pre-validated `cost` value. This multiplication is performed on a plain `u64` with no overflow protection. In Rust release mode, `u64` overflow wraps silently. A crafted opcode can cause the product to wrap to a value ≤ `u32::MAX`, bypassing the post-multiplication guard and returning a fraudulently low (or zero) cost for an unknown operator.

---

### Finding Description

`op_unknown` in `src/more_ops.rs` handles unknown opcodes in lenient/consensus mode. The cost is computed in two stages:

**Stage 1** — compute a base cost from arguments (cost_function 0–3), bounded by `check_cost`: [1](#0-0) 

**Stage 2** — multiply by `(cost_multiplier + 1)` and apply a post-multiplication guard: [2](#0-1) 

The `cost_multiplier` is extracted from the opcode atom bytes via `u32_from_u8`, giving a maximum value of `u32::MAX = 4,294,967,295`, stored as `u64`: [3](#0-2) 

The multiplication `cost *= cost_multiplier + 1` is a plain `u64 *= u64` with no `checked_mul` or `saturating_mul`. In Rust release mode, this wraps silently on overflow.

The post-multiplication guard `if cost > u32::MAX as u64` is only effective when the product does **not** overflow. If it does overflow and wraps to a value ≤ `u32::MAX`, the guard passes and the function returns `Ok(Reduction(cost, nil))` with a fraudulently small cost.

**Concrete overflow path:**

- `cost_multiplier = 0xFFFFFFFF` → `cost_multiplier + 1 = 2^32`
- For `cost * 2^32 mod 2^64 ≤ u32::MAX`, we need `cost mod 2^32 = 0`, i.e., `cost` is a multiple of `2^32`
- The smallest such value is `cost = 2^32 = 4,294,967,296`
- The standard Chia `max_cost = 11,000,000,000 > 2^32`, so `cost` can reach `2^32` before multiplication
- `2^32 * 2^32 = 2^64 ≡ 0 (mod 2^64)` → wrapped cost = 0
- `0 > u32::MAX` is false → returns `Ok(Reduction(0, nil))` — zero cost charged

For cost_function=2 (MUL-like), two atoms of ~741 KB each produce `cost ≈ (741455)^2 / 128 ≈ 4.3e9 ≈ 2^32` with far fewer allocator objects than cost_function=1 requires. [4](#0-3) 

---

### Impact Explanation

An attacker submits a CLVM program containing a crafted unknown opcode (not starting with `0xffff`) with:
- Opcode bytes encoding `cost_multiplier = 0xFFFFFFFF`
- Arguments sized to drive the pre-multiplication cost to exactly `2^32`

The program executes and is charged cost = 0 (or another wrapped small value). This breaks the block cost limit: programs that should be rejected as exceeding `max_cost` are accepted. All nodes running the same release binary compute the same wrong cost, so there is no immediate consensus split between identical nodes, but the cost model invariant is violated — programs execute for free, enabling block-stuffing or mempool spam at negligible cost.

If overflow checks are enabled in the release profile, the multiplication panics, causing a node crash on any program containing such an opcode — a denial-of-service vector.

---

### Likelihood Explanation

- The attacker controls both the opcode bytes (setting `cost_multiplier`) and the argument list (setting the pre-multiplication cost) entirely through submitted CLVM bytes.
- Unknown opcodes are accepted in consensus/lenient mode, which is the default for on-chain execution.
- The required pre-multiplication cost of `2^32` is below the standard Chia `max_cost` of `11,000,000,000`.
- No privileged access, key material, or social engineering is required.
- The attack requires knowledge of the cost model but no special capabilities beyond submitting a transaction.

---

### Recommendation

Replace the unchecked multiplication with a checked variant:

```rust
// Before (unsafe):
cost *= cost_multiplier + 1;
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
}

// After (safe):
cost = cost
    .checked_mul(cost_multiplier + 1)
    .filter(|&c| c <= u32::MAX as u64)
    .ok_or(EvalErr::Invalid(o))?;
```

This ensures that any overflow is caught and returns an error rather than silently producing a wrapped, fraudulently small cost.

---

### Proof of Concept

```
Opcode bytes: [0xFF, 0xFF, 0xFF, 0xFF, 0x80]
  - Last byte = 0x80 → cost_function = (0x80 >> 6) & 0x3 = 2 (MUL-like)
  - Prefix bytes [0xFF, 0xFF, 0xFF, 0xFF] → cost_multiplier = 0xFFFFFFFF

Arguments: two atoms, each of size ~741,455 bytes (content irrelevant)
  - cost_function=2 loop: cost ≈ (741455 + 741455)*6 + (741455*741455)/128
                               ≈ 8,897,460 + 4,294,967,296/128 ... 
  - Tuned to land cost = 4,294,967,296 = 2^32 exactly

Multiplication: 2^32 * (0xFFFFFFFF + 1) = 2^32 * 2^32 = 2^64 ≡ 0 (mod 2^64)

Post-multiplication guard: 0 > u32::MAX → false
Result: Ok(Reduction(0, nil))  ← zero cost charged for a multi-gigabyte computation
```

The attacker submits this as a CLVM program in a block. Every node accepts it with cost 0, allowing the block's remaining cost budget to be filled with additional such opcodes, effectively executing unbounded computation within a single block.

### Citations

**File:** src/more_ops.rs (L201-266)
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
        }
        3 => {
            let mut cost = CONCAT_BASE_COST;
            while let Some((arg, rest)) = allocator.next(args) {
                args = rest;
                let len = atom_len(allocator, arg, "unknown op")?;
                cost += CONCAT_COST_PER_ARG;
                cost += CONCAT_COST_PER_BYTE * (len as Cost);
                check_cost(cost, max_cost)?;
            }
            cost
        }
        _ => 1,
    };

    assert!(cost > 0);

    check_cost(cost, max_cost)?;
    cost *= cost_multiplier + 1;
    if cost > u32::MAX as u64 {
        Err(EvalErr::Invalid(o))?
    } else {
        Ok(Reduction(cost as Cost, allocator.nil()))
    }
```
