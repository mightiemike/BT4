### Title
`op_mod` Uses Wrong Cost Constants (`DIV_BASE_COST`/`DIV_COST_PER_BYTE` Instead of `DIVMOD_BASE_COST`/`DIVMOD_COST_PER_BYTE`), Causing Undercharged Execution — (`File: src/more_ops.rs`)

---

### Summary

`op_mod` (opcode 61) charges cost using `DIV_BASE_COST` and `DIV_COST_PER_BYTE`, but it performs the same underlying BigInt division as `op_divmod`. The correct cost constants are `DIVMOD_BASE_COST` and `DIVMOD_COST_PER_BYTE`. This is a direct wiring error: two distinct named constant pairs exist for the same operation class, and the wrong pair is wired to `op_mod`.

---

### Finding Description

In `src/more_ops.rs`, three related operators share the same division computation:

- `op_div` (opcode 19): computes `a0.div_floor(&a1)` — returns quotient
- `op_mod` (opcode 61): computes `a0.mod_floor(&a1)` — returns remainder
- `op_divmod` (opcode 20): computes `a0.div_mod_floor(&a1)` — returns both

The cost constants defined are:

```
DIVMOD_BASE_COST: Cost = 1116
DIVMOD_COST_PER_BYTE: Cost = 6

DIV_BASE_COST: Cost = 988
DIV_COST_PER_BYTE: Cost = 4
```

`op_divmod` correctly uses `DIVMOD_BASE_COST`/`DIVMOD_COST_PER_BYTE`: [1](#0-0) 

But `op_mod` uses the cheaper `DIV_BASE_COST`/`DIV_COST_PER_BYTE`: [2](#0-1) 

The same error is present in `op_mod_malachite`: [3](#0-2) 

In `num-bigint`, `mod_floor` internally performs the same division as `div_mod_floor` and discards the quotient — the CPU cost is identical. The size limits applied to `op_mod` are also identical to `op_divmod` (`a0_len > 256 || a1_len > 1024`), confirming the same computational profile is expected: [4](#0-3) [5](#0-4) 

The dispatch wiring in `chia_dialect.rs` confirms opcode 61 maps to `op_mod`: [6](#0-5) 

---

### Impact Explanation

`op_mod` is undercharged relative to its true CPU cost. For maximum-size inputs (`a0_len=256`, `a1_len=1024`):

- Charged cost: `988 + (256 + 1024) × 4 = 6,108`
- Correct cost: `1116 + (256 + 1024) × 6 = 8,796`
- Undercharge per call: **2,688 cost units (~30%)**

An attacker can craft a CLVM program that calls `mod` repeatedly with large operands. Within a fixed cost budget (e.g., the block cost limit), the program will perform ~30% more actual CPU work than the cost model permits. This is an undercharged execution vulnerability — a consensus-critical issue in a blockchain VM where cost accounting is the primary DoS defense.

---

### Likelihood Explanation

`op_mod` is a standard arithmetic operator available to any CLVM program submitted to the Chia mempool or included in a block. No special permissions or flags are required. An attacker only needs to submit a valid CLVM program that calls `mod` with large atom arguments. The entry path is fully attacker-controlled via the `argument_list` parameter passed through `ChiaDialect::op` → `op_mod`. [7](#0-6) 

---

### Recommendation

Replace `DIV_BASE_COST` and `DIV_COST_PER_BYTE` with `DIVMOD_BASE_COST` and `DIVMOD_COST_PER_BYTE` in both `op_mod` and `op_mod_malachite`:

```diff
-    let cost = DIV_BASE_COST + ((a0_len + a1_len) as Cost) * DIV_COST_PER_BYTE;
+    let cost = DIVMOD_BASE_COST + ((a0_len + a1_len) as Cost) * DIVMOD_COST_PER_BYTE;
```

Apply this fix in both the `num-bigint` path (`op_mod`, line 775) and the `malachite` path (`op_mod_malachite`, line 800). [8](#0-7) [9](#0-8) 

---

### Proof of Concept

```clvm
; CLVM program: call mod with max-size operands in a loop
; Each call costs 6108 instead of the correct 8796
; Within a budget of N, attacker gets ~30% more iterations than intended

(a
  (q . (a 2 (c 2 (c 5 (c 11 ())))))
  (c
    (q . (a
      (i 11
        (q . (a 2 (c 2 (c 5 (c (- 11 (q . 1)) ())))))
        (q . 1))
      1))
    (c
      (q . <256-byte-atom>)   ; dividend
      (c
        (q . <1024-byte-atom>) ; divisor
        (c (q . 1000) ())))))
```

Each iteration calls `(mod <256-byte-atom> <1024-byte-atom>)`, charged at 6,108 instead of 8,796. With a block cost limit of 11,000,000,000, an attacker gets approximately 1,800,000 iterations instead of the intended 1,250,000 — a 44% excess computation load on validators.

### Citations

**File:** src/more_ops.rs (L716-717)
```rust
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
```

**File:** src/more_ops.rs (L719-719)
```rust
    let cost = DIVMOD_BASE_COST + ((a0_len + a1_len) as Cost) * DIVMOD_COST_PER_BYTE;
```

**File:** src/more_ops.rs (L745-746)
```rust
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
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

**File:** src/chia_dialect.rs (L136-145)
```rust
    fn op(
        &self,
        allocator: &mut Allocator,
        o: NodePtr,
        argument_list: NodePtr,
        max_cost: Cost,
        extension: OperatorSet,
    ) -> Response {
        let flags = self.flags
            | match extension {
```

**File:** src/chia_dialect.rs (L245-245)
```rust
            61 => op_mod,
```
