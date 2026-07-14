### Title
`op_unknown` Cost Check Applied to Pre-Multiplied Value, Returned Cost Is Post-Multiplied — (`File: src/more_ops.rs`)

---

### Summary

In `op_unknown` (`src/more_ops.rs`), the cost-limit guard (`check_cost`) is called on the raw, pre-multiplied cost, but the value actually returned to the caller is the post-multiplied cost. This is a direct arithmetic-semantic-mismatch analog to the external report: a transformation is applied to a value *after* the check that was supposed to bound it, so the check is effectively bypassed.

---

### Finding Description

`op_unknown` computes a base cost from the opcode's `cost_function` field, then multiplies it by `(cost_multiplier + 1)` extracted from the opcode bytes. The ordering is:

```rust
// src/more_ops.rs  lines 258-266
assert!(cost > 0);

check_cost(cost, max_cost)?;          // ← guard on PRE-multiplied cost
cost *= cost_multiplier + 1;          // ← multiplier applied AFTER guard
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))   // ← POST-multiplied cost returned
}
```

`check_cost` simply tests `cost > max_cost` and returns `Err(CostExceeded)` if so. Because the guard fires on the pre-multiplied value, an attacker-controlled opcode byte-string can satisfy `check_cost(1, max_cost)` (trivially true) while encoding a `cost_multiplier` of up to `u32::MAX − 1`, causing the returned cost to be up to `u32::MAX = 4 294 967 295` — orders of magnitude above `max_cost`.

The only post-multiplication guard is the `u32::MAX` overflow check, which is not a cost-limit check; it is a representation-width check. It does not prevent the returned cost from exceeding `max_cost`.

The `cost_multiplier` is decoded from attacker-controlled opcode bytes:

```rust
// src/more_ops.rs  lines 201-207
let cost_function = (op[op.len() - 1] & 0b11000000) >> 6;
let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
    Some(v) => v as u64,
    None => { return Err(EvalErr::Invalid(o))?; }
};
```

Any opcode atom whose leading bytes encode a large `u32` value and whose final byte selects `cost_function = 0` (constant base cost = 1) will pass `check_cost(1, max_cost)` and return `cost = cost_multiplier + 1`, which can be up to `u32::MAX`.

---

### Impact Explanation

**Consensus divergence / incorrect cost accounting.** The cost returned by `op_unknown` is added to `current_cost` in the `run_program` loop. Because the returned cost can far exceed the remaining budget without triggering the internal guard, the accumulated cost after the operator returns can be wildly inconsistent with what the pre-execution cost estimate predicted. Any system that pre-validates cost (e.g., mempool admission) using a different code path or a different implementation will disagree with the execution result, producing a consensus split. Additionally, a program that should be accepted within budget may be rejected, or a program that should be rejected may slip through the internal guard and only fail later — at a different point than any reference implementation expects.

---

### Likelihood Explanation

Unknown opcodes are reachable in lenient/consensus mode (`allow_unknown_ops() == true`), which is the normal operating mode for full-node block validation in Chia. Any attacker who can submit a CLVM program (e.g., via a coin spend) can include an unknown opcode atom with a crafted multiplier. No special privileges are required. The opcode bytes are fully attacker-controlled.

---

### Recommendation

Move `check_cost` to after the multiplication, so the guard is applied to the value that will actually be returned:

```rust
cost *= cost_multiplier + 1;
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    check_cost(cost, max_cost)?;   // guard on POST-multiplied cost
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

---

### Proof of Concept

Craft an opcode atom whose bytes are `[0xFF, 0xFF, 0xFE, 0x00]` (4 bytes, `op_len == 4` is handled separately in `chia_dialect.rs`, so use a 5-byte atom instead):

- Bytes: `[0x00, 0xFF, 0xFF, 0xFF, 0x00]`
  - `op[0..4]` = `[0x00, 0xFF, 0xFF, 0xFF]` → `cost_multiplier = 0x00FFFFFF = 16777215`
  - `op[4]` = `0x00` → `cost_function = 0` (constant, base cost = 1)
- `check_cost(1, max_cost)` passes for any `max_cost >= 1`
- Returned cost = `1 * (16777215 + 1) = 16777216`

With `max_cost = 100`, `check_cost(1, 100)` passes, but the returned cost is `16777216` — 167,772× the allowed budget — and is handed back to the `run_program` accumulator without rejection by `op_unknown`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** src/cost.rs (L5-10)
```rust
pub fn check_cost(cost: Cost, max_cost: Cost) -> Result<()> {
    if cost > max_cost {
        Err(EvalErr::CostExceeded)
    } else {
        Ok(())
    }
```

**File:** src/run_program.rs (L441-449)
```rust
            let r = self.dialect.op(
                self.allocator,
                operator,
                operand_list,
                max_cost,
                current_extensions,
            )?;
            self.push(r.1)?;
            Ok(r.0)
```
