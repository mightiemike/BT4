### Title
`op_multiply` Cost Undercharge via `limbs_for_int` Sign-Byte Omission — (File: `src/more_ops.rs`)

### Summary

The `op_multiply` operator tracks the running product's byte size using `limbs_for_int(&total)`, which computes `v.bits().div_ceil(8)`. This omits the mandatory sign-extension byte that CLVM's signed big-endian encoding requires whenever the most significant bit of a positive product is set. As a result, the intermediate size variable `l0` underestimates the actual atom length by 1 byte at each such step, causing the cost charged for every subsequent multiplication to be lower than the actual computational work performed.

### Finding Description

**Vulnerability class:** Arithmetic semantic mismatch — a wrong fixed-formula value (`limbs_for_int`) is used instead of the actual dynamic value (true atom byte length), directly analogous to the BaseGauge report's use of a fixed `periodDuration` instead of the actual remaining time.

**Root cause in `src/more_ops.rs`:**

`limbs_for_int` is defined as:

```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize   // line 101
}
```

`BigInt::bits()` returns the number of bits in the **absolute value**, with no sign bit. For a positive number whose most significant bit is 1 (e.g., 255 = `0xff`, 65025 = `0xfe01`), CLVM's signed big-endian encoding requires a leading `0x00` byte to prevent misinterpretation as negative. `limbs_for_int` does not account for this byte, so it returns 1 less than the actual atom length for all such values.

The `int_atom` helper used for the **first** argument correctly returns `a.atom_len(args)` (the true wire length):

```rust
pub fn int_atom(a: &Allocator, args: NodePtr, op_name: &str) -> Result<(Number, usize)> {
    match a.sexp(args) {
        SExp::Atom => Ok((a.number(args), a.atom_len(args))),   // line 250 — actual length
        ...
    }
}
```

But after every subsequent multiplication, `l0` is updated with the underestimating function:

```rust
l0 = limbs_for_int(&total);   // line 649 — may be 1 less than actual atom length
if l0 > 1024 {
    return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
}
```

This `l0` is then fed into the cost formula for the **next** step:

```rust
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;          // line 615/623/643
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;  // line 616/624/644
```

**Concrete discrepancy confirmed by the existing test:**

```rust
// test_int_atom in op_utils.rs, line 423
#[case(0xffffff.into(), (0xffffff.into(), 4))]
```

`int_atom` correctly returns length 4 for `0xffffff` (stored as `0x00 0xff 0xff 0xff`). But `limbs_for_int(0xffffff)` = `ceil(24/8)` = **3**, underestimating by 1.

**Step-by-step trace with attacker-controlled input `(* 0x00ff 0x00ff 0x00ff ...)`:**

| Step | `total` | `limbs_for_int` (`l0`) | Actual atom length | Undercharge per step |
|------|---------|------------------------|-------------------|----------------------|
| 1st arg | 255 | 2 (from `int_atom`, correct) | 2 | — |
| 2nd arg | 65025 | `ceil(16/8)` = **2** | 3 (`0x00 0xfe 0x01`) | `1×6 + 2/128 = 6` |
| 3rd arg | ~16.5M | `ceil(24/8)` = **3** | 4 (`0x00 0xff ...`) | `1×6 + l1/128 ≈ 6` |
| … | … | … | … | ~6 per step |

The size-limit guard also uses the underestimated `l0`, so a product whose true atom length is 1025 bytes passes the `l0 > 1024` check when its high bit is set.

### Impact Explanation

Every multiplication step where the running product has its high bit set charges less cost than the actual bignum work performed. An attacker who crafts a sequence of multiplications keeping the product in this range can execute more computational work than the cost budget should allow. If any alternative CLVM implementation (Python reference, future Rust port) uses the true atom length for cost tracking, the two implementations will disagree on whether a given program is within budget — a direct consensus divergence. The corrupted value is the `Cost` returned by `op_multiply`, which feeds directly into the global cost accumulator in `run_program`.

### Likelihood Explanation

The trigger is fully attacker-controlled: any CLVM program byte sequence invoking `*` with arguments chosen to keep the running product's high bit set. No special permissions, flags, or social engineering are required. The `*` operator is a standard core operator reachable from any CLVM program submitted to the Chia mempool.

### Recommendation

Replace `limbs_for_int(&total)` with a function that accounts for the sign byte, matching the semantics of `int_atom`/`a.atom_len`. One approach:

```rust
// After: total *= ...;
// Replace:
l0 = limbs_for_int(&total);
// With:
let tmp = a.new_number(total.clone())?;
l0 = a.atom_len(tmp);
```

Alternatively, add a sign-byte correction directly:

```rust
fn atom_len_for_int(v: &Number) -> usize {
    let raw = v.bits().div_ceil(8) as usize;
    // positive numbers whose high bit is set need a leading 0x00
    if v.sign() == Sign::Plus && v.bits() % 8 == 0 {
        raw + 1
    } else {
        raw
    }
}
```

### Proof of Concept

Attacker submits the CLVM program `(* 0x00ff 0x00ff 0x00ff ...)` (N copies of the 2-byte atom `0x00ff` = 255).

- After step 1: `total = 255`, `l0 = limbs_for_int(255) = 1`; actual atom = `0x00 0xff` = **2 bytes**.
- Step 2 cost uses `l0 = 1` instead of 2: undercharge = `1 × MUL_LINEAR_COST_PER_BYTE` = **6 per step**.
- Over N steps the total undercharge is `≈ 6N`, allowing the program to perform more bignum multiplications than the cost limit should permit.

The corrupted output is the `Cost` field of the `Reduction` returned by `op_multiply` at [1](#0-0) , driven by the underestimating helper at [2](#0-1) , while the correct length is available via `int_atom` at [3](#0-2) .

### Citations

**File:** src/more_ops.rs (L100-102)
```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
```

**File:** src/more_ops.rs (L649-655)
```rust
        l0 = limbs_for_int(&total);
        if l0 > 1024 {
            return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
        }
    }
    let total = a.new_number(total)?;
    Ok(malloc_cost(a, cost, total))
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
