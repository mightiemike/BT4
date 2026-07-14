### Title
Unchecked `cost_multiplier` Multiplication in `op_unknown` Allows Undercharged Execution via u64 Overflow — (File: `src/more_ops.rs`)

### Summary
`op_unknown` in `src/more_ops.rs` computes the cost of unknown opcodes in two independent stages: a base cost bounded by `max_cost`, then an unchecked multiplication by `cost_multiplier + 1`. Because the multiplication is not guarded against u64 overflow, an attacker-controlled opcode can cause the product to wrap around to a value far below the true mathematical cost — including zero — bypassing the cost cap and enabling undercharged execution in consensus mode.

### Finding Description

`op_unknown` is the lenient-mode handler for unknown CLVM opcodes. Its cost model has two independently computed components:

**Stage 1 — base cost** (bounded by `max_cost`):

```rust
// src/more_ops.rs lines 209-260
let mut cost = match cost_function {
    0 => 1,
    1 => { /* ARITH_BASE_COST + n*ARITH_COST_PER_ARG + bytes*ARITH_COST_PER_BYTE */ }
    2 => { /* MUL-like */ }
    3 => { /* CONCAT-like */ }
    _ => 1,
};
assert!(cost > 0);
check_cost(cost, max_cost)?;   // ← cost is confirmed ≤ max_cost here
```

**Stage 2 — multiplier application** (unchecked):

```rust
// src/more_ops.rs lines 261-266
cost *= cost_multiplier + 1;   // ← NO overflow guard
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`cost_multiplier` is extracted from the opcode bytes as a `u32` cast to `u64`, so `cost_multiplier + 1` can be up to `2^32 = 4,294,967,296`. The Chia block cost limit is 11,000,000,000 (~11 billion), so `cost` after `check_cost` can be up to ~11 billion. The product `11 × 10^9 × 4.3 × 10^9 ≈ 47 × 10^18` exceeds `u64::MAX ≈ 18.4 × 10^18`.

In Rust release builds, integer overflow wraps silently. The wrapped result is then compared only against `u32::MAX`, not against `max_cost`. If the wrapped value is ≤ `u32::MAX`, the function returns it as the final cost — which can be orders of magnitude below the true mathematical cost, or even zero.

**Concrete trigger**: set `cost_multiplier = u32::MAX` (opcode prefix bytes `0xff 0xff 0xff 0xff`) and craft arguments so the base cost equals exactly `2^32`:

```
cost = 2^32
cost_multiplier + 1 = 2^32
cost * (cost_multiplier + 1) = 2^64 ≡ 0 (mod 2^64)
```

`0 ≤ u32::MAX` → the function returns `Ok(Reduction(0, nil))`. The operation costs zero.

The base cost `2^32 = 4,294,967,296` is reachable with cost_function 1 (add-like):

```
cost = 99 + n × 320 + total_bytes × 3 = 4,294,967,296
```

This is satisfiable with ~13.4 million argument atoms of appropriate sizes, all within the allocator's limits and the block cost budget.

### Impact Explanation

An attacker can include unknown opcodes in a CLVM program that should consume a large fraction of the block cost budget but instead consume zero (or near-zero) cost. Because `op_unknown` is a no-op returning nil, the attacker gains no computational power, but the cost accounting is corrupted: the transaction appears far cheaper than it is. This allows:

1. **Undercharged execution**: a transaction that should exhaust the block cost limit passes as nearly free, allowing many more such transactions per block than the protocol intends.
2. **Consensus divergence between build profiles**: in debug builds the multiplication panics; in release builds it wraps silently. A node running a debug build would reject the transaction while a release build accepts it, breaking consensus.

### Likelihood Explanation

The attack requires only attacker-controlled CLVM bytes submitted to a full node in consensus mode (lenient/`allow_unknown_ops` mode). No privileged access is needed. The opcode encoding is fully documented in the source comments. Crafting the exact argument sizes to hit a target base cost is straightforward arithmetic. The `LIMIT_SOFTFORK` flag does not affect `op_unknown`. The only constraint is that the base cost must reach `≥ u64::MAX / (cost_multiplier + 1)` before the multiplier is applied, which is achievable within the Chia block cost limit.

### Recommendation

Replace the unchecked multiplication with a checked variant and enforce that the result does not exceed `max_cost`:

```rust
// src/more_ops.rs, after line 260
let Some(multiplied) = cost.checked_mul(cost_multiplier + 1) else {
    return Err(EvalErr::Invalid(o));
};
cost = multiplied;
check_cost(cost, max_cost)?;   // enforce the budget cap on the final cost
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

This mirrors the pattern already used for the value accumulator in `op_add`'s fast path (`checked_add`) and ensures the two independently computed cost components — base cost and multiplier — cannot together produce a value that escapes the `max_cost` bound.

### Proof of Concept

Entry path: attacker submits CLVM bytes → `run_program` → `apply_op` (line 523) → `dialect.op` → `op_unknown`.

Opcode bytes: `[0xff, 0xff, 0xff, 0xff, 0x40]`
- Last byte `0x40` → `cost_function = (0x40 >> 6) = 1` (add-like cost)
- Prefix bytes `[0xff, 0xff, 0xff, 0xff]` → `cost_multiplier = u32::MAX = 4,294,967,295`
- `cost_multiplier + 1 = 2^32`

Arguments: enough atom arguments with total byte count chosen so that:
```
ARITH_BASE_COST + n × ARITH_COST_PER_ARG + total_bytes × ARITH_COST_PER_BYTE = 2^32
99 + n × 320 + total_bytes × 3 = 4,294,967,296
```

After `check_cost(cost, max_cost)?` passes (cost = 2^32 ≤ max_cost = 11 billion):

```rust
cost *= cost_multiplier + 1;
// 2^32 * 2^32 = 2^64 ≡ 0 (mod 2^64) in release mode
// cost == 0
if cost > u32::MAX as u64 { ... }  // false: 0 ≤ u32::MAX
Ok(Reduction(0, allocator.nil()))  // returned cost = 0
```

The operation is accepted with zero cost. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** src/run_program.rs (L522-523)
```rust
            cost += match op {
                Operation::Apply => self.apply_op(cost, effective_max_cost - cost)?,
```
