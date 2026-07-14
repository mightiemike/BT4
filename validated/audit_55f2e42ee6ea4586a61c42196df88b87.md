### Title
Unchecked u64 Multiplication Overflow in `op_unknown` Produces Incorrect Cost — (`File: src/more_ops.rs`)

### Summary
`op_unknown` in `src/more_ops.rs` applies a caller-controlled cost multiplier via an unchecked `u64 * u64` multiplication. In Rust release builds, this silently wraps on overflow, producing a cost of 0 (or another small value) that passes the post-multiplication guard. An attacker can craft a CLVM program with a specific unknown opcode and argument list to trigger the overflow, causing the operation to be accepted with a near-zero cost instead of its true cost.

### Finding Description

`op_unknown` computes a base cost (bounded by `max_cost`) and then multiplies it by `cost_multiplier + 1`, where `cost_multiplier` is derived directly from the opcode bytes and can be up to `0xffffffff` (i.e., `cost_multiplier + 1` reaches `2^32 = 4_294_967_296`): [1](#0-0) 

```rust
let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
    Some(v) => v as u64,
    None => { return Err(EvalErr::Invalid(o))?; }
};
```

After computing the base cost and calling `check_cost`, the multiplier is applied with no overflow guard: [2](#0-1) 

```rust
check_cost(cost, max_cost)?;
cost *= cost_multiplier + 1;          // ← unchecked u64 × u64
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`Cost` is `u64`: [3](#0-2) 

In Rust release mode, `u64` overflow wraps silently. The post-multiplication guard only rejects values `> u32::MAX`. If the product wraps to a value `≤ u32::MAX`, the guard passes and the operation returns with the wrapped (incorrect) cost.

**Concrete overflow path:**

- Chia's `max_cost` is `6_000_000_000`, which is greater than `2^32 = 4_294_967_296`.
- An attacker crafts an unknown opcode with `cost_multiplier = 0xffffffff`, so `cost_multiplier + 1 = 2^32`.
- The attacker passes arguments to `cost_function = 1` (add-like cost) such that the base cost equals exactly `2^32 = 4_294_967_296`. This passes `check_cost` since `4_294_967_296 < 6_000_000_000`.
- The multiplication: `4_294_967_296 × 4_294_967_296 = 2^64 ≡ 0 (mod 2^64)`.
- `0 > u32::MAX` is false, so the function returns `Ok(Reduction(0, nil))` — cost zero.

The base cost for `cost_function = 1` accumulates as: [4](#0-3) 

With `ARITH_BASE_COST = 99`, `ARITH_COST_PER_ARG = 320`, `ARITH_COST_PER_BYTE = 3`, reaching a base cost of `4_294_967_296` requires approximately 13.3 million 1-byte arguments (`(4_294_967_296 - 99) / 323 ≈ 13_296_498`). The `check_cost` inside the loop enforces `cost ≤ max_cost` at each step, so the loop terminates with the target base cost. [5](#0-4) 

### Impact Explanation

The returned `Reduction.0` is the cost charged to the caller's running total in `run_program`. If this value is 0 (or any value far below the true cost), the cost accounting for the entire program is wrong. Two concrete impacts:

1. **Cost undercharging / block-size bypass**: An attacker can include many such operations in a block, each appearing to cost 0, while the true computational budget is exhausted. This allows more operations per block than the protocol intends.

2. **Consensus divergence**: Any CLVM implementation that uses arbitrary-precision integers (e.g., a reference Python implementation) would compute the correct large cost and reject the block as exceeding `max_cost`, while all Rust nodes accept it with cost 0. This is a hard consensus split.

### Likelihood Explanation

The attacker-controlled entry path is direct: the opcode bytes and argument list are part of the CLVM program, which is fully attacker-controlled. The opcode byte layout for `cost_multiplier` is documented in the code comments: [6](#0-5) 

Unknown opcodes in lenient/consensus mode are processed by `op_unknown`. The only constraint is constructing a program large enough to accumulate the required base cost, which is within the allocator's capacity given Chia's `max_cost` of 6 billion.

### Recommendation

Replace the unchecked multiplication with a checked or saturating variant, and reject immediately if the product overflows or exceeds `u32::MAX`:

```rust
let final_cost = (cost as u128) * (cost_multiplier as u128 + 1);
if final_cost > u32::MAX as u128 {
    return Err(EvalErr::Invalid(o))?;
}
Ok(Reduction(final_cost as Cost, allocator.nil()))
```

Alternatively, use `cost.checked_mul(cost_multiplier + 1)` and treat `None` as an invalid opcode.

### Proof of Concept

Craft an unknown opcode atom with:
- Bytes `[0xff, 0xff, 0xff, 0xff, 0x40]` — last byte `0x40` sets `cost_function = 1` (bits 7–6), `cost_multiplier = 0xffffffff`.
- Pass ~13.3 million 1-byte atom arguments so that the `cost_function = 1` loop accumulates a base cost of exactly `4_294_967_296`.

Call `op_unknown(allocator, opcode_node, args_node, 6_000_000_000)`.

Expected (correct) behavior: cost = `4_294_967_296 × 4_294_967_296` → rejected as `> u32::MAX`.
Actual (buggy) behavior in release mode: `4_294_967_296 × 4_294_967_296 = 2^64 ≡ 0 (mod 2^64)` → `0 > u32::MAX` is false → returns `Ok(Reduction(0, nil))`. [2](#0-1)

### Citations

**File:** src/more_ops.rs (L160-267)
```rust
pub fn op_unknown(
    allocator: &mut Allocator,
    o: NodePtr,
    mut args: NodePtr,
    max_cost: Cost,
) -> Response {
    // unknown opcode in lenient mode
    // unknown ops are reserved if they start with 0xffff
    // otherwise, unknown ops are no-ops, but they have costs. The cost is computed
    // like this:

    // byte index (reverse):
    // | 4 | 3 | 2 | 1 | 0          |
    // +---+---+---+---+------------+
    // | multiplier    |XX | XXXXXX |
    // +---+---+---+---+---+--------+
    //  ^               ^    ^
    //  |               |    + 6 bits ignored when computing cost
    // cost_multiplier  |
    // (up to 4 bytes)  + 2 bits
    //                    cost_function

    // 1 is always added to the multiplier before using it to multiply the cost, this
    // is since cost may not be 0.

    // cost_function is 2 bits and defines how cost is computed based on arguments:
    // 0: constant, cost is 1 * (multiplier + 1)
    // 1: computed like operator add, multiplied by (multiplier + 1)
    // 2: computed like operator mul, multiplied by (multiplier + 1)
    // 3: computed like operator concat, multiplied by (multiplier + 1)

    // this means that unknown ops where cost_function is 1, 2, or 3, may still be
    // fatal errors if the arguments passed are not atoms.

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
}
```

**File:** src/cost.rs (L3-3)
```rust
pub type Cost = u64;
```
