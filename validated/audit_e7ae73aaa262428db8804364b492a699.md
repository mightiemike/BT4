### Title
`op_multiply` Cost Undercharge via Unit Mismatch in Running-Product Size Tracking — (`File: src/more_ops.rs`)

---

### Summary

`op_multiply` initialises its running-size variable `l0` in **bytes** (from `int_atom`), but after every multiplication step it overwrites `l0` with `limbs_for_int(&total)`, which returns the number of 64-bit **limbs** (8 bytes each). The cost formula then uses `l0` as if it were still in bytes. Because limbs ≈ bytes/8, the per-step cost charged for large multi-operand multiplications is undercharged by roughly 8×, allowing an attacker to submit programs that should exceed the cost limit but pass it.

---

### Finding Description

In `src/more_ops.rs`, `op_multiply` tracks the byte-width of the running product in `l0`:

**First iteration** — `l0` is set to the byte length of the first argument via `int_atom`:

```rust
(total, l0) = int_atom(a, arg, "*")?;   // l0 = atom_len() → bytes
if l0 > 256 { return Err(...); }
```

`int_atom` is defined in `src/op_utils.rs` as:

```rust
pub fn int_atom(a: &Allocator, args: NodePtr, op_name: &str) -> Result<(Number, usize)> {
    match a.sexp(args) {
        SExp::Atom => Ok((a.number(args), a.atom_len(args))),   // usize = byte count
        ...
    }
}
```

**Every subsequent iteration** — after the multiplication is performed, `l0` is silently overwritten with the limb count of the running product:

```rust
l0 = limbs_for_int(&total);   // l0 = limb count (64-bit words), NOT bytes
if l0 > 1024 { return Err(...); }
```

The cost formula applied in every non-first iteration uses `l0` as if it were still in bytes:

```rust
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

where `l1` is always the **byte** length of the current operand (`buf.len()` or `len_for_value(val)`). After the first step, `l0` is in limbs (≈ bytes/8), so the quadratic term `l0 * l1` is undercharged by ≈ 64× and the linear term by ≈ 8×.

The inconsistency is confirmed by the two guard limits: the first-argument guard uses `l0 > 256` (bytes), while the running-product guard uses `l0 > 1024` (limbs = 8 192 bytes). If both were in the same unit the limits would be comparable; the 4× ratio only makes sense when the units differ.

This is the direct analog of the external report's root cause: a value that should be tracked internally with a consistent unit is instead read from an external/derived source (`limbs_for_int`) that returns a different unit, causing the arithmetic that depends on it (cost accounting) to produce a systematically wrong result.

---

### Impact Explanation

**Impact: High**

An attacker who understands the cost model can craft a CLVM program of the form `(* A B C D …)` where `A` is a large atom (up to 256 bytes) and `B, C, D, …` are chosen so that the running product grows into the multi-limb range. Because `l0` is in limbs after the first step, the charged cost for each subsequent operand is ≈ 8× lower than intended. A program that should cost, say, 8× the block cost limit will instead be charged only 1× and will be accepted.

Concrete consequences:
- **Consensus divergence**: nodes running a reference Python CLVM implementation (which charges costs in bytes throughout) will reject the program; nodes running `clvm_rs` will accept it. A block containing such a transaction will be valid on one set of nodes and invalid on another, splitting the chain.
- **Resource exhaustion**: the actual CPU work performed (bignum multiplication) is proportional to the true byte-size product, not the undercharged limb-size product, so accepted programs consume far more CPU than the cost limit was designed to allow.

---

### Likelihood Explanation

**Likelihood: Medium**

The attacker needs only to submit a valid CLVM program using the standard `*` operator with multiple large-integer operands — a completely normal, permissionless operation on the Chia network. No privileged keys, social engineering, or special configuration is required. The only constraint is knowing the cost model well enough to craft operands that exploit the unit gap, which is straightforward from the public cost constants.

---

### Recommendation

Replace the `limbs_for_int` call with a byte-length measurement that is consistent with the unit used in the cost formula. The simplest fix is to track `l0` as the byte length of the running product throughout:

```rust
// After each multiplication, compute byte length of the product
// instead of limb count:
l0 = (total.bits() as usize + 7) / 8;   // or equivalent byte-length helper
if l0 > 8192 { return Err(...); }        // adjust limit to match byte unit
```

Alternatively, convert `l0` to bytes before using it in the cost formula, and keep the limb-based limit check separate. Either way, the unit of `l0` must be consistent (bytes) throughout the entire function.

---

### Proof of Concept

Consider a CLVM program `(* A B B B … B)` where:
- `A` = a 256-byte atom (maximum allowed first argument)
- `B` = a 2-byte atom (e.g., `0x7fff`)
- The list has `N` copies of `B`

**Step 1 (first iteration):** `l0 = 256` (bytes). No cost charged yet (first arg is free of `MUL_COST_PER_OP`).

**Step 2 (second iteration):** `l1 = 2` bytes.
- Charged: `(256 + 2) * LINEAR + (256 * 2) / SQUARE_DIV` — correct.
- After: `l0 = limbs_for_int(&total)`. If the product is ~258 bytes, `l0 ≈ 33` limbs.

**Step 3 (third iteration):** `l1 = 2` bytes.
- Charged: `(33 + 2) * LINEAR + (33 * 2) / SQUARE_DIV` — should be `(258 + 2) * LINEAR + (258 * 2) / SQUARE_DIV`.
- Undercharge ratio: `260 / 35 ≈ 7.4×` on the linear term; `516 / 66 ≈ 7.8×` on the quadratic term.

After `N` steps the cumulative undercharge compounds. A program designed to consume 8× the block cost limit in true CPU work will be charged only ~1× and accepted by `clvm_rs`, while a byte-correct implementation rejects it — producing a consensus split. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** src/more_ops.rs (L595-605)
```rust
    let mut l0: usize = 0;
    while let Some((arg, rest)) = a.next(input) {
        input = rest;
        if first_iter {
            (total, l0) = int_atom(a, arg, "*")?;
            if l0 > 256 {
                return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
            }
            first_iter = false;
            continue;
        }
```

**File:** src/more_ops.rs (L615-617)
```rust
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                check_cost(cost, max_cost)?;
```

**File:** src/more_ops.rs (L649-652)
```rust
        l0 = limbs_for_int(&total);
        if l0 > 1024 {
            return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
        }
```

**File:** src/op_utils.rs (L248-256)
```rust
pub fn int_atom(a: &Allocator, args: NodePtr, op_name: &str) -> Result<(Number, usize)> {
    match a.sexp(args) {
        SExp::Atom => Ok((a.number(args), a.atom_len(args))),
        _ => Err(EvalErr::InvalidOpArg(
            args,
            format!("Requires Int Argument: {op_name}"),
        ))?,
    }
}
```
