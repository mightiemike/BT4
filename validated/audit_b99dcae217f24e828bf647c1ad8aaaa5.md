### Title
`LIMIT_HEAP` Flag Defined and Included in `MEMPOOL_MODE` but Never Enforced in Allocator or Execution Engine — (`src/chia_dialect.rs`, `src/run_program.rs`)

---

### Summary

`ClvmFlags::LIMIT_HEAP` is declared with an explicit security purpose ("limits the number of atom-bytes allowed to be allocated, as well as the number of pairs"), is included in the `MEMPOOL_MODE` constant used for strict mempool evaluation, but is **never checked or enforced** anywhere in the allocator or execution engine. The flag is wired in but dead — a direct analog to the ShortLongSpell `sellSlippage` bug where a protection parameter is validated/included but never applied.

---

### Finding Description

In `src/chia_dialect.rs`, `LIMIT_HEAP` is defined as:

```rust
/// When set, limits the number of atom-bytes allowed to be allocated,
/// as well as the number of pairs.
const LIMIT_HEAP = 0x0004;
``` [1](#0-0) 

It is included in `MEMPOOL_MODE`:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
``` [2](#0-1) 

Every other flag in `MEMPOOL_MODE` is concretely enforced:

- `NO_UNKNOWN_OPS` — checked in `unknown_operator()` and `RuntimeDialect::op()` [3](#0-2) 
- `DISABLE_OP` — checked for op 60 (`modpow`) and inside `op_div`/`op_divmod`/`op_mod` for size limits [4](#0-3) 
- `CANONICAL_INTS` — enforced in `uint_atom()` to reject non-canonical leading zeros [5](#0-4) 
- `LIMIT_SOFTFORK` — enforced in `apply_op()` to cap softfork nesting depth at 20 [6](#0-5) 

`LIMIT_HEAP` has **no corresponding enforcement site** in `src/allocator.rs`, `src/run_program.rs`, or any operator file. The `Dialect::flags()` method returns it, and `run_program` calls `self.dialect.flags()` only to pass flags to `uint_atom` for softfork cost parsing — not to enforce heap limits. [7](#0-6) 

The `Allocator` has no concept of a byte or pair cap gated on this flag. No call site in `run_program.rs` reads `LIMIT_HEAP` and applies a limit to `new_atom`, `new_pair`, or any allocation primitive.

---

### Impact Explanation

An attacker-controlled CLVM program submitted to the mempool can allocate an unbounded number of atom-bytes and pairs. The `LIMIT_HEAP` flag is supposed to cap this in mempool mode, but since it is never enforced, the protection is entirely absent. Concretely:

1. **Memory exhaustion / OOM**: A crafted program can force the node's mempool evaluator to allocate gigabytes of heap, crashing or severely degrading the node.
2. **Consensus divergence**: If a future patch enforces `LIMIT_HEAP` on some nodes but not others, programs that previously succeeded will fail on patched nodes, splitting consensus.
3. **Mempool DoS**: Repeated submission of heap-exhausting programs bypasses the intended mempool resource cap, making the node's mempool evaluation unbounded in memory cost.

---

### Likelihood Explanation

The entry path is direct and requires no special privilege: any participant can submit a CLVM spend bundle to the mempool. The program bytes are attacker-controlled. The `run_program` call in mempool mode uses `MEMPOOL_MODE` flags, which includes `LIMIT_HEAP`, but since the flag is never checked, the attacker simply needs to write a program that allocates large atoms or many pairs (e.g., via repeated `concat` or `cons` operations). This is trivially constructible.

---

### Recommendation

Enforce `LIMIT_HEAP` in the `Allocator` or in `run_program`. Concretely, the `Allocator` should track total allocated atom bytes and pair count, and when `LIMIT_HEAP` is set in the active dialect flags, `new_atom` and `new_pair` should return an error once the limits are exceeded. The limits should be defined as named constants alongside the flag definition in `src/chia_dialect.rs`.

---

### Proof of Concept

1. Construct a CLVM program that repeatedly `concat`s large atoms, e.g.:
   ```
   (concat <4096-byte-atom> <4096-byte-atom> ... )
   ```
   nested or looped via `apply` to produce megabytes of allocation.

2. Evaluate it with `ChiaDialect::new(MEMPOOL_MODE)` via `run_program`.

3. Observe that the allocator grows without bound — no `LIMIT_HEAP` check fires anywhere in `src/run_program.rs` or `src/allocator.rs` — despite `LIMIT_HEAP` being set in the flags passed to the dialect.

The flag is received by `ChiaDialect` [8](#0-7)  and returned by `flags()`, but no code path in the execution engine reads `flags().contains(ClvmFlags::LIMIT_HEAP)` and acts on it, making the protection entirely inert.

### Citations

**File:** src/chia_dialect.rs (L36-38)
```rust
        /// as well as the number of pairs.
        const LIMIT_HEAP = 0x0004;

```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L85-89)
```rust
    if flags.contains(ClvmFlags::NO_UNKNOWN_OPS) {
        Err(EvalErr::Unimplemented(o))?
    } else {
        op_unknown(allocator, o, args, max_cost)
    }
```

**File:** src/chia_dialect.rs (L97-99)
```rust
    pub fn new(flags: ClvmFlags) -> ChiaDialect {
        ChiaDialect { flags }
    }
```

**File:** src/chia_dialect.rs (L240-243)
```rust
                if flags.contains(ClvmFlags::DISABLE_OP) {
                    return Err(EvalErr::Unimplemented(o))?;
                }
                op_modpow
```

**File:** src/op_utils.rs (L67-79)
```rust
            if flags.contains(ClvmFlags::CANONICAL_INTS) {
                // strip potential zero
                if buf[0] == 0 {
                    if buf.len() < 2 || (buf[1] & 0x80) == 0 {
                        return Err(EvalErr::InvalidOpArg(
                            args,
                            format!(
                                "{op_name} requires u{0} arg with no leading zeros",
                                SIZE * 8
                            ),
                        ));
                    }
                    buf = &buf[1..];
```

**File:** src/run_program.rs (L385-390)
```rust
            let expected_cost = uint_atom::<8>(
                self.allocator,
                first(self.allocator, operand_list)?,
                "softfork",
                self.dialect.flags(),
            )?;
```

**File:** src/run_program.rs (L415-418)
```rust
            if self.dialect.flags().contains(ClvmFlags::LIMIT_SOFTFORK)
                && self.softfork_stack.len() >= 20
            {
                return Err(EvalErr::SoftforkStackDepthExceeded);
```
