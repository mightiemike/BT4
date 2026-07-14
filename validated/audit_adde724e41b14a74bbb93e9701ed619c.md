### Title
`op_mod` Uses Wrong Cost Constants (`DIV_BASE_COST`/`DIV_COST_PER_BYTE` Instead of `DIVMOD_BASE_COST`/`DIVMOD_COST_PER_BYTE`), Causing Systematic Cost Undercharging - (`File: src/more_ops.rs`)

---

### Summary

`op_mod` (opcode 61) computes the floor-modulo of two big integers. Internally, `mod_floor()` performs the same underlying floor-division as `div_mod_floor()` (used by `op_divmod`). However, `op_mod` charges cost using `DIV_BASE_COST` (988) and `DIV_COST_PER_BYTE` (4) — the constants for the cheaper `op_div` operator — instead of `DIVMOD_BASE_COST` (1116) and `DIVMOD_COST_PER_BYTE` (6). This is the exact same bug class as the Tapioca H-5 report: a semantically similar but numerically smaller constant is substituted in a formula, causing the computed value (here: execution cost) to be lower than the actual work performed.

---

### Finding Description

`op_divmod` (opcode 20) and `op_mod` (opcode 61) both perform floor division on arbitrary-precision integers. `op_divmod` calls `div_mod_floor()` and returns both quotient and remainder. `op_mod` calls `mod_floor()` and returns only the remainder. In `num-bigint` (and `malachite-bigint`), `mod_floor` is implemented by computing the full floor-division internally — the same CPU work as `div_mod_floor`.

Despite performing equivalent computation, the two operators are charged differently:

| Operator | Base Cost | Per-Byte Cost |
|---|---|---|
| `op_divmod` (opcode 20) | `DIVMOD_BASE_COST` = **1116** | `DIVMOD_COST_PER_BYTE` = **6** |
| `op_div` (opcode 19) | `DIV_BASE_COST` = **988** | `DIV_COST_PER_BYTE` = **4** |
| `op_mod` (opcode 61) | `DIV_BASE_COST` = **988** ← **wrong** | `DIV_COST_PER_BYTE` = **4** ← **wrong** |

`op_mod` uses the `op_div` constants instead of the `op_divmod` constants. The undercharge per call is:

```
Δbase  = 1116 − 988 = 128
Δbyte  = (6 − 4) × (a0_len + a1_len)
```

At maximum allowed operand sizes (`a0_len = 256`, `a1_len = 1024`), the undercharge reaches **2,688 cost units per call**. Both the `num-bigint` path and the `malachite-bigint` path (`op_mod_malachite`) contain the identical wrong constants.

The root cause is in `src/more_ops.rs` at lines 775 and 800:

```rust
// op_mod (num-bigint path) — line 775
let cost = DIV_BASE_COST + ((a0_len + a1_len) as Cost) * DIV_COST_PER_BYTE;

// op_mod_malachite path — line 800
let cost = DIV_BASE_COST + ((a0_len + a1_len) as Cost) * DIV_COST_PER_BYTE;
```

Compare with the correct `op_divmod` formula at line 719:

```rust
let cost = DIVMOD_BASE_COST + ((a0_len + a1_len) as Cost) * DIVMOD_COST_PER_BYTE;
```

---

### Impact Explanation

CLVM cost is the consensus-critical resource limit. Every full node enforces a maximum cost per block/spend. If `op_mod` is undercharged, an attacker can craft a CLVM program that:

1. **Exceeds the intended CPU budget** — programs using `op_mod` with near-maximum operands consume more real CPU time than the cost limit was designed to allow, enabling a denial-of-service vector against full nodes.
2. **Causes consensus divergence** — if a future implementation or alternative CLVM evaluator correctly charges `DIVMOD_BASE_COST`/`DIVMOD_COST_PER_BYTE` for `op_mod`, it will reject programs that the current `clvm_rs` accepts (or vice versa), breaking consensus.

The corrupted result is the `Cost` value returned inside the `Reduction` from `op_mod`, which is lower than the actual computational work performed.

---

### Likelihood Explanation

`op_mod` is a live, reachable opcode (opcode 61) wired directly in `ChiaDialect::op()`. Any attacker who can submit a CLVM spend to the mempool can trigger this path with attacker-controlled atom bytes. No special privileges are required. The operand size limits (`a0_len ≤ 256`, `a1_len ≤ 1024`) are enforced after the cost check, so the undercharge applies to all valid inputs up to those limits.

---

### Recommendation

Replace `DIV_BASE_COST` and `DIV_COST_PER_BYTE` with `DIVMOD_BASE_COST` and `DIVMOD_COST_PER_BYTE` in both `op_mod` and `op_mod_malachite`:

```rust
// op_mod — line 775
let cost = DIVMOD_BASE_COST + ((a0_len + a1_len) as Cost) * DIVMOD_COST_PER_BYTE;

// op_mod_malachite — line 800
let cost = DIVMOD_BASE_COST + ((a0_len + a1_len) as Cost) * DIVMOD_COST_PER_BYTE;
```

---

### Proof of Concept

**Wrong constants in `op_mod`:** [1](#0-0) 

`DIVMOD_BASE_COST` = 1116, `DIVMOD_COST_PER_BYTE` = 6 (for `op_divmod`); `DIV_BASE_COST` = 988, `DIV_COST_PER_BYTE` = 4 (for `op_div`).

**`op_mod` uses the cheaper `op_div` constants (line 775):** [2](#0-1) 

**`op_mod_malachite` has the same wrong constants (line 800):** [3](#0-2) 

**`op_divmod` correctly uses `DIVMOD_BASE_COST`/`DIVMOD_COST_PER_BYTE` (line 719):** [4](#0-3) 

**`op_mod` is live at opcode 61 in `ChiaDialect::op()`:** [5](#0-4)

### Citations

**File:** src/more_ops.rs (L52-56)
```rust
const DIVMOD_BASE_COST: Cost = 1116;
const DIVMOD_COST_PER_BYTE: Cost = 6;

const DIV_BASE_COST: Cost = 988;
const DIV_COST_PER_BYTE: Cost = 4;
```

**File:** src/more_ops.rs (L706-731)
```rust
pub fn op_divmod(a: &mut Allocator, input: NodePtr, max_cost: Cost, flags: ClvmFlags) -> Response {
    if flags.contains(ClvmFlags::MALACHITE) {
        return op_divmod_malachite(a, input, max_cost, flags);
    }
    let [v0, v1] = get_args::<2>(a, input, "divmod")?;
    let (a0, a0_len) = int_atom(a, v0, "divmod")?;
    let (a1, a1_len) = int_atom(a, v1, "divmod")?;
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
    }
    let cost = DIVMOD_BASE_COST + ((a0_len + a1_len) as Cost) * DIVMOD_COST_PER_BYTE;
    check_cost(cost, max_cost)?;
    if a1.sign() == Sign::NoSign {
        return Err(EvalErr::DivisionByZero(input));
    }
    let (q, r) = a0.div_mod_floor(&a1);
    let q1 = a.new_number(q)?;
    let r1 = a.new_number(r)?;

    let c = (a.atom_len(q1) + a.atom_len(r1)) as Cost * MALLOC_COST_PER_BYTE;
    let r: NodePtr = a.new_pair(q1, r1)?;
    Ok(Reduction(cost + c, r))
}
```

**File:** src/more_ops.rs (L762-783)
```rust
pub fn op_mod(a: &mut Allocator, input: NodePtr, max_cost: Cost, flags: ClvmFlags) -> Response {
    if flags.contains(ClvmFlags::MALACHITE) {
        return op_mod_malachite(a, input, max_cost, flags);
    }
    let [v0, v1] = get_args::<2>(a, input, "mod")?;
    let (a0, a0_len) = int_atom(a, v0, "mod")?;
    let (a1, a1_len) = int_atom(a, v1, "mod")?;
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
    }
    let cost = DIV_BASE_COST + ((a0_len + a1_len) as Cost) * DIV_COST_PER_BYTE;
    check_cost(cost, max_cost)?;
    if a1.sign() == Sign::NoSign {
        return Err(EvalErr::DivisionByZero(input));
    }
    let q = a.new_number(a0.mod_floor(&a1))?;
    let c = a.atom_len(q) as Cost * MALLOC_COST_PER_BYTE;
    Ok(Reduction(cost + c, q))
}
```

**File:** src/more_ops.rs (L785-808)
```rust
fn op_mod_malachite(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    flags: ClvmFlags,
) -> Response {
    let [v0, v1] = get_args::<2>(a, input, "mod")?;
    let (a0, a0_len) = malachite_int_atom(a, v0, "mod")?;
    let (a1, a1_len) = malachite_int_atom(a, v1, "mod")?;
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
    }
    let cost = DIV_BASE_COST + ((a0_len + a1_len) as Cost) * DIV_COST_PER_BYTE;
    check_cost(cost, max_cost)?;
    if a1.sign() == malachite_bigint::Sign::NoSign {
        return Err(EvalErr::DivisionByZero(input));
    }
    let q = a.new_malachite_number(a0.mod_floor(&a1))?;
    let c = a.atom_len(q) as Cost * MALLOC_COST_PER_BYTE;
    Ok(Reduction(cost + c, q))
}
```

**File:** src/chia_dialect.rs (L245-245)
```rust
            61 => op_mod,
```
