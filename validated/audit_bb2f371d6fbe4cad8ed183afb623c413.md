### Title
Wrong Scaling in `op_multiply` Cost Tracker Due to Mixed Byte-Length Units — (File: `src/more_ops.rs`)

---

### Summary

`op_multiply` uses two incompatible definitions of "byte length" for the running-product size variable `l0`: the **serialized** byte length (from `int_atom`, which includes a leading zero sign byte for positive numbers whose high bit is set) for the first argument, and the **magnitude** byte length (from `limbs_for_int`, which strips the sign byte) for every subsequent step. This is a direct arithmetic-semantic-mismatch analog of the external report's wrong-decimal-scale bug: a quantity computed in one unit is silently reused in a context that expects a different unit.

---

### Finding Description

In `op_multiply` (`src/more_ops.rs`), `l0` is the cost-model proxy for the size of the running product:

**Initialization — serialized byte length (includes sign byte):** [1](#0-0) 

`int_atom` is defined as: [2](#0-1) 

It returns `a.atom_len(args)` — the actual stored byte length. For a positive number whose high bit is set (e.g., 128 = `0x0080`), CLVM's signed big-endian encoding requires a leading `0x00` byte, so `int_atom` returns **2**.

**Update after each multiplication — magnitude byte length (strips sign byte):** [3](#0-2) 

`limbs_for_int` is defined as: [4](#0-3) 

Its own test confirms it returns `bytes.len() - 1` when the first byte is a zero sign byte: [5](#0-4) 

So for the value 128 (`0x0080`): `bits(128) = 8`, `8.div_ceil(8) = 1` — **one byte fewer** than `int_atom` returns.

**The cost formula that consumes `l0`:** [6](#0-5) 

After the first multiplication, every subsequent step uses `l0 = limbs_for_int(&total)`, which is the magnitude byte length of the running product. Whenever the running product is a positive number with its high bit set (e.g., `128^5 = 0x800000000`), `limbs_for_int` returns one fewer byte than the serialized length. The cost formula then undercharges by `l1 × MUL_LINEAR_COST_PER_BYTE` (= `l1 × 6`) per step.

The same inconsistency exists in the `no-fastpath` branch, where `l1` is taken from `int_atom` (serialized length) while `l0` comes from `limbs_for_int` (magnitude length): [7](#0-6) 

---

### Impact Explanation

The CLVM cost model is the sole DoS-prevention mechanism for Chia full nodes: programs are rejected when accumulated cost exceeds `max_cost`. Undercharging `op_multiply` means an attacker can submit programs that perform more bignum multiplication work than the cost limit is supposed to allow. For a crafted sequence of multiplications where the running product repeatedly crosses a power-of-two byte boundary (causing `limbs_for_int` to lag the true serialized size by one byte), the undercharge per step is up to `l1 × 6` cost units, where `l1 ≤ 256` bytes. Over many steps this allows measurably more computation than the budget permits, weakening the DoS bound.

---

### Likelihood Explanation

Any CLVM program submitted to a Chia node can invoke `op_multiply`. The trigger condition — a running product whose high bit is set — occurs naturally for any sequence of multiplications involving numbers ≥ 128. An attacker who understands the cost model can deliberately construct inputs that maximize the undercharge. No special privileges are required; the entry path is fully attacker-controlled CLVM bytes.

---

### Recommendation

Replace the `int_atom`-derived initial `l0` with `limbs_for_int` applied to the parsed first argument, so that `l0` is in the same unit (magnitude byte length) throughout the entire loop:

```rust
if first_iter {
    let (val, _serialized_len) = int_atom(a, arg, "*")?;
    l0 = limbs_for_int(&val);   // consistent with subsequent updates
    if l0 > 256 { return Err(...); }
    total = val;
    first_iter = false;
    continue;
}
```

Alternatively, replace `limbs_for_int` with a helper that returns the serialized byte length (magnitude + 1 if high bit set) everywhere, making `l0` consistently represent the CLVM-encoded size.

---

### Proof of Concept

Consider `(* 0x0080 <256-byte-arg>)`:

- `int_atom` sets `l0 = 2` (serialized length of `0x0080`).
- Cost for the 256-byte second argument: `(2 + 256) × 6 + (2 × 256)/128 = 1548 + 4 = 1552`.

Now consider `(* 0x007f <256-byte-arg>)`:

- `int_atom` sets `l0 = 1` (serialized length of `0x7f`).
- Cost: `(1 + 256) × 6 + (1 × 256)/128 = 1542 + 2 = 1544`.

After the first multiplication in a longer chain, `l0` is updated via `limbs_for_int`. For a running product equal to `128^5 = 34359738368 = 0x800000000` (serialized as 6 bytes `0x00 0x80 0x00 0x00 0x00 0x00`), `limbs_for_int` returns **5** (magnitude bytes), not 6. The next step is therefore charged as if the accumulator were 5 bytes wide, undercharging by `l1 × 6` cost units — up to `256 × 6 = 1536` units per step — relative to what the cost model intends.

### Citations

**File:** src/more_ops.rs (L100-102)
```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
```

**File:** src/more_ops.rs (L109-116)
```rust
    // redundant leading zeros don't count, since they aren't stored internally
    let expected = if !bytes.is_empty() && bytes[0] == 0 {
        bytes.len() - 1
    } else {
        bytes.len()
    };
    assert_eq!(limbs_for_int(&bigint), expected);
}
```

**File:** src/more_ops.rs (L598-604)
```rust
        if first_iter {
            (total, l0) = int_atom(a, arg, "*")?;
            if l0 > 256 {
                return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
            }
            first_iter = false;
            continue;
```

**File:** src/more_ops.rs (L615-617)
```rust
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                check_cost(cost, max_cost)?;
```

**File:** src/more_ops.rs (L636-648)
```rust
        #[cfg(feature = "no-fastpath")]
        {
            let (n1, l1) = int_atom(a, arg, "*")?;
            let l1 = l1 as u64;
            if l1 > 256 {
                return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
            }
            cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
            cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
            check_cost(cost, max_cost)?;

            total *= n1;
        }
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
