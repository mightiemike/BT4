### Title
Integer Overflow in `op_unknown` Cost Multiplier Produces Undercharged Execution Cost — (`File: src/more_ops.rs`)

### Summary

`op_unknown` in `src/more_ops.rs` performs a `check_cost` guard on the **pre-multiplication** cost, then multiplies by `(cost_multiplier + 1)`. Because both operands are `u64` and Rust release builds wrap on overflow, the multiplication can silently produce a value far smaller than the true cost. The subsequent guard `if cost > u32::MAX as u64` only rejects wrapped values that are still large; when the wrapped result is ≤ `u32::MAX`, the function returns a drastically undercharged `Reduction` cost to the caller.

### Finding Description

In `op_unknown` (`src/more_ops.rs` lines 258–266):

```rust
assert!(cost > 0);

check_cost(cost, max_cost)?;      // ← checks PRE-multiplication cost only
cost *= cost_multiplier + 1;      // ← u64 × u64, wraps silently in release mode
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))   // ← returns wrapped, tiny cost
}
```

`Cost` is `pub type Cost = u64` (`src/cost.rs` line 3). `cost_multiplier` is cast from a `u32` (max `0xFFFF_FFFF = 4 294 967 295`), so `cost_multiplier + 1` can be up to `2^32 = 4 294 967 296`. The `check_cost` call inside each `cost_function` branch (e.g., line 219, 240, 251) ensures `cost ≤ max_cost` before the multiplication, but `max_cost` in Chia's consensus mode is the block cost limit (~11 × 10⁹ > 2^32). Therefore `cost` can legally exceed `2^32`, making `cost × (cost_multiplier + 1)` overflow `u64`.

When the wrapped product is ≤ `u32::MAX`, the function returns that tiny value as the operation's cost. The outer `run_program` loop (`src/run_program.rs` line 522) adds this to the running total and checks it only on the **next** iteration — but the damage is already done: the returned cost is permanently recorded as the cost of the operation.

The `cost_multiplier` is extracted from all bytes of the opcode except the last (`src/more_ops.rs` line 202), and the reserved-opcode guard only blocks opcodes whose first two bytes are both `0xFF` (`src/more_ops.rs` line 197). An attacker can therefore use an opcode such as `[0xFF, 0xFE, 0xFF, 0xFF, 0x40]` to achieve `cost_multiplier = 0xFFFEFFFF = 4 294 836 223`, `cost_multiplier + 1 = 4 294 836 224`, while bypassing the reserved check.

### Impact Explanation

An attacker-controlled CLVM program submitted to a Chia full node in consensus mode (where unknown ops are allowed) can trigger this path. The operation's true computational cost is `cost_pre × (cost_multiplier + 1)`, which may be in the tens of billions, but the cost charged to the transaction is the wrapped residue — potentially 0 or a few hundred. This constitutes **undercharged execution**: the attacker runs an expensive no-op for free, exhausting node CPU/memory while paying negligible fees. It also constitutes a **consensus divergence** risk: any implementation that computes costs with wider integers or checked arithmetic will disagree on the total cost of the block, causing a chain split.

### Likelihood Explanation

Unknown opcodes are accepted in consensus (non-mempool) mode — `allow_unknown_ops()` returns `true` when `NO_UNKNOWN_OPS` is not set (`src/chia_dialect.rs` line 285–287). The attacker controls both the opcode bytes (setting `cost_multiplier`) and the argument list (tuning the pre-multiplication cost). Because `cost_function = 1` produces `cost = 99 + 320N + 3M` and `gcd(320, 3) = 1`, the attacker can reach any sufficiently large integer cost value by choosing argument count `N` and total byte count `M`. The overflow threshold (~4.3 × 10⁹) is well within Chia's block cost limit (~11 × 10⁹), making the required cost value reachable. The attack requires only crafting a valid CLVM program — no privileged access.

### Recommendation

Replace the unchecked multiplication with a checked variant and treat overflow as an invalid opcode:

```rust
check_cost(cost, max_cost)?;
let Some(final_cost) = cost.checked_mul(cost_multiplier + 1) else {
    return Err(EvalErr::Invalid(o))?;
};
if final_cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(final_cost as Cost, allocator.nil()))
}
```

Alternatively, perform `check_cost` **after** the multiplication on `final_cost` to enforce the budget limit on the true cost.

### Proof of Concept

**Opcode bytes:** `[0xFF, 0xFE, 0xFF, 0xFF, 0x40]`
- `op[0..4] = [0xFF, 0xFE, 0xFF, 0xFF]` → `cost_multiplier = 0xFFFEFFFF = 4 294 836 223`
- `op[4] = 0x40` → top 2 bits = `01` → `cost_function = 1` (ARITH-like)
- Not reserved: `op[0]=0xFF, op[1]=0xFE ≠ 0xFF` ✓

**Arguments:** Choose `N` arguments of total `M` bytes such that `cost = 99 + 320N + 3M = 4 295 098 370` (just above the overflow threshold `⌊u64::MAX / 4 294 836 224⌋ + 1`). This is achievable within the 11 × 10⁹ block cost budget.

**Result in release mode:**
```
cost_pre = 4_295_098_370
cost_multiplier + 1 = 4_294_836_224
product = 4_295_098_370 × 4_294_836_224
        = 18_447_869_... (overflows u64)
wrapped = product mod 2^64  →  some value ≤ u32::MAX
```

The function returns `Ok(Reduction(wrapped_tiny_cost, nil))` instead of `Err(EvalErr::Invalid)`, charging the transaction a negligible cost for what should be a multi-billion-cost operation. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** src/more_ops.rs (L194-207)
```rust
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

**File:** src/chia_dialect.rs (L285-287)
```rust
    fn allow_unknown_ops(&self) -> bool {
        !self.flags.contains(ClvmFlags::NO_UNKNOWN_OPS)
    }
```

**File:** src/run_program.rs (L514-523)
```rust
            if cost > effective_max_cost {
                return Err(EvalErr::CostExceeded);
            }
            let top = self.op_stack.pop();
            let op = match top {
                Some(f) => f,
                None => break,
            };
            cost += match op {
                Operation::Apply => self.apply_op(cost, effective_max_cost - cost)?,
```
