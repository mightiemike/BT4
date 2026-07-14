### Title
`op_ash` and `op_lsh` Ignore `max_cost`, Performing Expensive Bignum Computation Before Cost Budget Enforcement — (File: `src/more_ops.rs`)

### Summary

Both `op_ash` and `op_lsh` in `src/more_ops.rs` accept a `max_cost` parameter but never use it (declared as `_max_cost`). They perform the full bignum shift computation and heap allocation unconditionally, then compute and return the cost after the fact — never calling `check_cost()`. An attacker-controlled CLVM program can trigger up to a 65535-bit shift (producing an ~8192-byte result) with zero remaining cost budget, causing validators to perform real computation and memory allocation that is not gated by the cost limit.

### Finding Description

`op_ash` at line 918 and `op_lsh` at line 982 of `src/more_ops.rs` both declare their cost parameter as `_max_cost: Cost`, signaling it is intentionally unused:

```rust
pub fn op_ash(a: &mut Allocator, input: NodePtr, _max_cost: Cost, _flags: ClvmFlags) -> Response {
    let [n0, n1] = get_args::<2>(a, input, "ash")?;
    let (i0, l0) = int_atom(a, n0, "ash")?;
    let a1 = i32_atom(a, n1, "ash")?;
    if !(-65535..=65535).contains(&a1) {
        return Err(EvalErr::ShiftTooLarge(n1));
    }
    let v: Number = if a1 > 0 { i0 << a1 } else { i0 >> -a1 };  // full computation done here
    let l1 = limbs_for_int(&v);
    let r = a.new_number(v)?;                                      // heap allocation done here
    let cost = ASHIFT_BASE_COST + ((l0 + l1) as Cost) * ASHIFT_COST_PER_BYTE;
    Ok(malloc_cost(a, cost, r))                                    // cost returned, never checked
}
```

`op_lsh` is structurally identical. Neither function ever calls `check_cost(cost, max_cost)`.

This is in direct contrast to every other cost-bearing operator in the file. For example, `op_sha256` calls `check_cost(cost, max_cost)?` inside its argument loop before hashing. `op_multiply`, `op_add`, `op_concat`, `op_logand`, and all others call `check_cost` during or before their expensive work.

The `run_program` loop in `src/run_program.rs` passes the remaining budget as `max_cost` to each operator:

```rust
cost += match op {
    Operation::Apply => self.apply_op(cost, effective_max_cost - cost)?,
```

The loop checks `if cost > effective_max_cost` only at the top of each iteration, **after** the operator has already returned. So if `op_ash` is invoked when the remaining budget is 1 (or 0), it still performs the full shift and allocation, returns a cost of up to ~107,000 (for a 65535-bit shift on a non-trivial input), and the overrun is only caught on the next loop iteration — after the work is done.

### Impact Explanation

An attacker-controlled CLVM program can craft a sequence that exhausts the cost budget to within 1 unit, then invokes `ash` or `lsh` with a maximum shift of 65535 bits. The interpreter performs:

- A bignum left-shift producing a result up to ~8192 bytes
- A heap allocation of ~8192 bytes in the `Allocator`
- A cost return of `ASHIFT_BASE_COST + (l0 + l1) * ASHIFT_COST_PER_BYTE + l1 * MALLOC_COST_PER_BYTE` ≈ 107,000 cost units

All of this work is done before the cost overrun is detected. The program then fails with `CostExceeded`, but the validator has already performed computation and allocation that was not authorized by the cost budget. This is an undercharged-execution vulnerability: real CPU and memory resources are consumed without being gated by the cost limit, allowing an attacker to extract free validator work on every submitted transaction.

### Likelihood Explanation

The attack requires only a valid CLVM program submitted to the Chia blockchain — no privileged access, no special keys, no social engineering. The attacker needs to know the cost of their preamble operations (all costs are deterministic and public) to land at exactly `budget - 1` before calling `ash`. This is straightforward to compute offline. The attack is repeatable across any number of transactions.

### Recommendation

Add a pre-computation cost upper-bound check in both `op_ash` and `op_lsh` before performing the shift. Since the output size is bounded by `l0 + shift_bits / 8`, an upper bound can be computed from the input size and shift amount before doing any bignum work:

```rust
// Before performing the shift:
let max_output_bytes = l0 + (a1.unsigned_abs() as usize / 8) + 1;
let upper_cost = ASHIFT_BASE_COST + ((l0 + max_output_bytes) as Cost) * ASHIFT_COST_PER_BYTE
    + max_output_bytes as Cost * MALLOC_COST_PER_BYTE;
check_cost(upper_cost, max_cost)?;
```

Alternatively, match the pattern used by all other operators: call `check_cost` with the exact computed cost immediately after computing it, before returning.

### Proof of Concept

The following CLVM program, submitted with any `max_cost` value, causes `op_ash` to perform a full 65535-bit shift and ~8192-byte allocation regardless of the remaining budget:

```
(ash 500 65535)
```

From the op-test fixture at `op-tests/test-more-ops.txt` line 263, this is a valid, accepted call that produces an ~8192-byte result. With `max_cost` set to any value less than the actual cost of this operation (~107,000), `op_ash` still completes the computation and allocation before `run_program` detects the overrun on the next loop iteration.

---

**Root cause:** `op_ash` (`src/more_ops.rs:918`) and `op_lsh` (`src/more_ops.rs:982`) declare `_max_cost` (unused), perform full bignum shift and heap allocation unconditionally, and never call `check_cost`. [1](#0-0) 

**Contrast with correct pattern** — `op_sha256` calls `check_cost` inside its loop before doing any hashing work: [2](#0-1) 

**`op_lsh` has the identical defect:** [3](#0-2) 

**`run_program` passes remaining budget as `max_cost` to each operator, but only checks the returned cost after the operator returns:** [4](#0-3) 

**Shift limit of 65535 bits is enforced, bounding the maximum output to ~8192 bytes, but no cost check is performed before that work:** [5](#0-4) 

**Cost constants confirm the magnitude of uncharged work:** `ASHIFT_BASE_COST = 596`, `ASHIFT_COST_PER_BYTE = 3`, `MALLOC_COST_PER_BYTE = 10` — for a 65535-bit shift on a 2-byte input, total cost ≈ 107,000 units, all incurred before any budget check. [6](#0-5)

### Citations

**File:** src/more_ops.rs (L62-66)
```rust
const ASHIFT_BASE_COST: Cost = 596;
const ASHIFT_COST_PER_BYTE: Cost = 3;

const LSHIFT_BASE_COST: Cost = 277;
const LSHIFT_COST_PER_BYTE: Cost = 3;
```

**File:** src/more_ops.rs (L399-408)
```rust
    let mut hasher = Sha256::new();
    while let Some((arg, rest)) = a.next(input) {
        input = rest;
        cost += SHA256_COST_PER_ARG;
        let blob = atom(a, arg, "sha256")?;
        cost += blob.as_ref().len() as Cost * SHA256_COST_PER_BYTE;
        check_cost(cost, max_cost)?;
        hasher.update(blob);
    }
    new_atom_and_cost(a, cost, &hasher.finalize())
```

**File:** src/more_ops.rs (L918-930)
```rust
pub fn op_ash(a: &mut Allocator, input: NodePtr, _max_cost: Cost, _flags: ClvmFlags) -> Response {
    let [n0, n1] = get_args::<2>(a, input, "ash")?;
    let (i0, l0) = int_atom(a, n0, "ash")?;
    let a1 = i32_atom(a, n1, "ash")?;
    if !(-65535..=65535).contains(&a1) {
        return Err(EvalErr::ShiftTooLarge(n1));
    }

    let v: Number = if a1 > 0 { i0 << a1 } else { i0 >> -a1 };
    let l1 = limbs_for_int(&v);
    let r = a.new_number(v)?;
    let cost = ASHIFT_BASE_COST + ((l0 + l1) as Cost) * ASHIFT_COST_PER_BYTE;
    Ok(malloc_cost(a, cost, r))
```

**File:** src/more_ops.rs (L982-1000)
```rust
pub fn op_lsh(a: &mut Allocator, input: NodePtr, _max_cost: Cost, _flags: ClvmFlags) -> Response {
    let [n0, n1] = get_args::<2>(a, input, "lsh")?;
    let b0_atom = atom(a, n0, "lsh")?;
    let b0 = b0_atom.as_ref();
    let a1 = i32_atom(a, n1, "lsh")?;
    if !(-65535..=65535).contains(&a1) {
        return Err(EvalErr::ShiftTooLarge(n1));
    }
    let i0 = BigUint::from_bytes_be(b0);
    let l0 = b0.len();
    let i0: Number = i0.into();

    let v: Number = if a1 > 0 { i0 << a1 } else { i0 >> -a1 };

    let l1 = limbs_for_int(&v);
    let r = a.new_number(v)?;
    let cost = LSHIFT_BASE_COST + ((l0 + l1) as Cost) * LSHIFT_COST_PER_BYTE;
    Ok(malloc_cost(a, cost, r))
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
