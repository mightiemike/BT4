### Title
u64 Overflow in `op_unknown` Cost Multiplier Allows Cost-Limit Bypass — (`File: src/more_ops.rs`)

---

### Summary

The `op_unknown` function in `src/more_ops.rs` computes a final cost by multiplying an accumulated loop cost by `(cost_multiplier + 1)`. Because both values are `u64` and Rust release builds use wrapping arithmetic on overflow, a crafted unknown opcode can cause this product to wrap to zero (or a value ≤ `u32::MAX`), causing the function to return `Ok(Reduction(0, nil))` after doing up to `max_cost` units of real work. The running cost accumulator in `run_program` is not incremented, so the attacker's budget is not consumed, and they can repeat the trick or execute further operations within the same `max_cost` envelope.

---

### Finding Description

In `src/more_ops.rs`, `op_unknown` computes cost in two phases:

**Phase 1 — loop (bounded by `check_cost`):**

```rust
let mut cost = match cost_function {
    1 => {
        let mut cost = ARITH_BASE_COST;          // 99
        while let Some((arg, rest)) = allocator.next(args) {
            cost += ARITH_COST_PER_ARG;          // +320 per arg
            byte_count += len as u64;
            check_cost(cost + (byte_count as Cost * ARITH_COST_PER_BYTE), max_cost)?;
        }
        cost + (byte_count * ARITH_COST_PER_BYTE)
    }
    ...
};
```

`check_cost` only fires when `cost > max_cost`, so `cost` can legally equal `max_cost` when the loop exits.

**Phase 2 — unchecked multiplication:**

```rust
check_cost(cost, max_cost)?;   // passes if cost == max_cost
cost *= cost_multiplier + 1;   // ← wrapping u64 multiply, no overflow guard
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`cost_multiplier` is extracted from the opcode bytes as a `u32` (max `0x7fffffff` without triggering the `0xffff` reserved prefix check), so `cost_multiplier + 1` can be `2^31`. If the loop is driven to `cost = 2^33 = 8,589,934,592` (reachable when `max_cost ≥ 8.6 billion, e.g. the Chia 11-billion block limit), then:

```
cost * (cost_multiplier + 1) = 2^33 * 2^31 = 2^64 ≡ 0  (mod 2^64)
```

The wrapped value `0 ≤ u32::MAX`, so the function returns `Ok(Reduction(0, nil))`.

**Exact trigger:**

| Field | Value |
|---|---|
| Opcode bytes | `[0x7f, 0xff, 0xff, 0xff, 0x40]` |
| `cost_multiplier` | `0x7fffffff = 2^31 − 1` |
| `cost_function` | `1` (add-like, bits 7–6 of last byte = `0b01`) |
| Args needed | 26,843,545 nil args + 31 total payload bytes to reach `cost = 2^33` exactly |
| Reported cost | `0` |

The opcode does not start with `0xff 0xff`, so the reserved-opcode guard does not fire.

---

### Impact Explanation

The `max_cost` parameter is the sole resource-exhaustion guard for CLVM execution on the Chia blockchain. By reporting cost `0` for an operation that consumed up to `max_cost` units of real work, an attacker can:

1. **Execute programs whose true computational cost exceeds `max_cost`** — each crafted `op_unknown` call consumes up to `max_cost` work but charges `0`, so the attacker can chain multiple such calls and do `n × max_cost` real work while staying within the budget.
2. **Cause consensus divergence** — if different CLVM implementations (Python reference, clvm_rs) handle the overflow differently, they will disagree on whether a block is valid, breaking consensus.
3. **Exhaust validator resources** — validators spend real CPU time iterating through millions of arguments while the cost meter does not advance.

This is directly analogous to the original report: just as `voter_admin` could mint unlimited VOTES to inflate `totalSupply` and bypass the governance threshold, an attacker here can craft a program that inflates real work while keeping the reported cost at zero, bypassing `max_cost`.

---

### Likelihood Explanation

- Unknown opcodes are permitted in consensus mode (block validation) when `NO_UNKNOWN_OPS` is not set; `MEMPOOL_MODE` does set this flag, but block validation does not.
- The trigger is fully deterministic and requires only crafting a specific 5-byte opcode and a list of ~26.8 million nil arguments (~26.8 MB serialized). This is within the range of Chia block sizes.
- No privileged access, social engineering, or dependency compromise is required — any transaction submitter can include this puzzle.
- The overflow is silent in release builds (Rust wrapping semantics); no panic or error is raised.

---

### Recommendation

Replace the unchecked multiplication with a checked or saturating variant, and validate the result before the `u32::MAX` comparison:

```rust
// Before (vulnerable):
cost *= cost_multiplier + 1;
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
}

// After (safe):
cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
}
```

Alternatively, cap `cost` before multiplication using `check_cost(cost, max_cost)?` and then verify the product does not exceed `max_cost` after multiplication.

---

### Proof of Concept

**Opcode construction:**

```
opcode = [0x7f, 0xff, 0xff, 0xff, 0x40]
         ^^^^^^^^^^^^^^^^^^^  ^^^^
         cost_multiplier      cost_function=1 (bits 7-6 = 01)
         = 0x7fffffff = 2^31-1
```

**Argument list:** 26,843,514 nil args + 31 one-byte args (total bytes = 31), so that:

```
cost = 99 + 26,843,545 × 320 + 31 × 3
     = 99 + 8,589,934,400 + 93
     = 8,589,934,592
     = 2^33
```

**Overflow:**

```
cost × (cost_multiplier + 1) = 2^33 × 2^31 = 2^64 ≡ 0  (mod 2^64, Rust wrapping)
0 ≤ u32::MAX  →  Ok(Reduction(0, nil))
```

**Effect in `run_program`:** the running `cost` variable is incremented by `0`, leaving the full `max_cost` budget available for subsequent operations. Repeating the call `k` times allows `k × max_cost` real work at reported cost `0`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** src/chia_dialect.rs (L78-90)
```rust
fn unknown_operator(
    allocator: &mut Allocator,
    o: NodePtr,
    args: NodePtr,
    flags: ClvmFlags,
    max_cost: Cost,
) -> Response {
    if flags.contains(ClvmFlags::NO_UNKNOWN_OPS) {
        Err(EvalErr::Unimplemented(o))?
    } else {
        op_unknown(allocator, o, args, max_cost)
    }
}
```
