### Title
Missing Default Softfork Depth Guard Allows Unbounded Stack Growth — (`src/run_program.rs`, `src/chia_dialect.rs`)

### Summary
The `LIMIT_SOFTFORK` flag, which enforces a maximum softfork nesting depth of 20, is not set in `ChiaDialect::default()`. Because the default dialect uses `ClvmFlags::empty()`, the depth-limiting guard is never active unless a caller explicitly opts in. An attacker can submit attacker-controlled CLVM bytes containing arbitrarily deeply nested `softfork` operators, growing the `softfork_stack` Vec to a size bounded only by the cost limit — which can be very large — causing excessive memory consumption and potential consensus divergence between nodes with different memory capacities.

---

### Finding Description

`ChiaDialect::default()` initialises the dialect with `ClvmFlags::empty()`: [1](#0-0) 

The softfork depth guard in `apply_op` is conditioned on `LIMIT_SOFTFORK` being present in the flags: [2](#0-1) 

Because `ClvmFlags::empty()` does not include `LIMIT_SOFTFORK`, the guard is never reached. Every time a `softfork` operator is evaluated, a new `SoftforkGuard` is pushed onto `softfork_stack` with no upper bound: [3](#0-2) 

The `SoftforkGuard` struct holds at minimum an `expected_cost` (u64), an `allocator_state: Checkpoint`, and an `operator_set: OperatorSet`: [4](#0-3) 

The only bound on nesting depth is the cost limit. Each softfork level adds `GUARD_COST` (observed as ~140 units in tests) plus a minimum `expected_cost` of 1. With a typical Chia block cost ceiling of ~11 billion, an attacker can nest approximately `11,000,000,000 / 141 ≈ 78,000,000` softfork levels, each consuming at least ~20 bytes of heap, totalling ~1.5 GB of allocator and stack memory — all within the declared cost budget.

The analog to the external report is direct: just as `_mintHook` defaults to `address(0)` (no restriction) until `setMintHook` is called, `LIMIT_SOFTFORK` defaults to absent (no restriction) until a caller explicitly sets it. In both cases the protective guard is opt-in rather than opt-out, and the window before it is set is exploitable.

---

### Impact Explanation

Nodes running with `ChiaDialect::new(ClvmFlags::empty())` — the default — will allocate memory proportional to softfork nesting depth. A crafted transaction that stays within the declared cost limit can exhaust available memory on resource-constrained nodes. If some full nodes OOM-crash while others (with more RAM) continue, the network experiences a **consensus divergence**: the crashing nodes reject the block while surviving nodes accept it, splitting the chain. This is not ordinary DoS — it is a consensus-safety violation triggered by a single attacker-controlled CLVM program.

---

### Likelihood Explanation

The attack requires only crafting a valid CLVM program with deeply nested `softfork` operators and submitting it as a transaction. No special privileges, keys, or social engineering are needed. The program is fully attacker-controlled. The only prerequisite is that the target node uses the default `ChiaDialect` without explicitly setting `LIMIT_SOFTFORK`, which is the common case for any caller relying on `ChiaDialect::default()`. [1](#0-0) 

---

### Recommendation

Set `LIMIT_SOFTFORK` in the default flag set so that the depth guard is active unless a caller explicitly opts out:

```rust
impl Default for ChiaDialect {
    fn default() -> Self {
        ChiaDialect {
            flags: ClvmFlags::LIMIT_SOFTFORK,
        }
    }
}
```

Alternatively, remove the flag entirely and unconditionally enforce the 20-level depth limit in `apply_op`, making the protection non-optional.

---

### Proof of Concept

A CLVM program of the form:

```
(softfork (q . <TOTAL_COST>) (q . 0) (q .
  (softfork (q . <INNER_COST>) (q . 0) (q .
    ... ; repeated ~78 million times within cost budget
  ) (q . ()))
) (q . ()))
```

submitted to a node using `ChiaDialect::new(ClvmFlags::empty())` will push ~78 million `SoftforkGuard` entries onto `softfork_stack`, consuming ~1.5 GB of memory, while reporting a cost that is within the declared block limit. The depth check at: [2](#0-1) 

is never reached because `ClvmFlags::LIMIT_SOFTFORK` is absent from the default dialect: [1](#0-0)

### Citations

**File:** src/chia_dialect.rs (L102-108)
```rust
impl Default for ChiaDialect {
    fn default() -> Self {
        ChiaDialect {
            flags: ClvmFlags::empty(),
        }
    }
}
```

**File:** src/run_program.rs (L83-98)
```rust
struct SoftforkGuard {
    // This is the expected cost of the program when exiting the guard. i.e. the
    // current_cost + the first argument to the operator
    expected_cost: Cost,

    // When exiting a softfork guard, all values used inside it are zapped. This
    // was the state of the allocator before entering. We restore to this state
    // on exit.
    allocator_state: Checkpoint,

    // this specifies which new operators are available
    operator_set: OperatorSet,

    #[cfg(test)]
    start_cost: Cost,
}
```

**File:** src/run_program.rs (L415-419)
```rust
            if self.dialect.flags().contains(ClvmFlags::LIMIT_SOFTFORK)
                && self.softfork_stack.len() >= 20
            {
                return Err(EvalErr::SoftforkStackDepthExceeded);
            }
```

**File:** src/run_program.rs (L421-427)
```rust
            self.softfork_stack.push(SoftforkGuard {
                expected_cost: current_cost + expected_cost,
                allocator_state: self.allocator.checkpoint(),
                operator_set: ext,
                #[cfg(test)]
                start_cost: current_cost,
            });
```
