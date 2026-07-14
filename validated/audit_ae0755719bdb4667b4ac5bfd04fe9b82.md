### Title
`DISABLE_OP` Flag Included in `MEMPOOL_MODE` Silently Blocks Valid Consensus Operator `op_modpow` (Opcode 60) — (File: `src/chia_dialect.rs`)

---

### Summary

The `DISABLE_OP` flag (`0x200`) is bundled into `MEMPOOL_MODE`, the default strict mode used for mempool validation. When active, it causes opcode 60 (`op_modpow`) to return `EvalErr::Unimplemented` rather than execute. However, `op_modpow` is a fully wired consensus operator — present in the main dispatch table with no hard-fork guard and its own calibrated cost model. This creates a consensus/mempool divergence: any puzzle spending a coin via `op_modpow` is valid on-chain but is unconditionally rejected by every node running in mempool mode, making those coins unspendable through normal transaction submission.

---

### Finding Description

In `src/chia_dialect.rs`, `DISABLE_OP` is defined and silently folded into `MEMPOOL_MODE`:

```rust
// src/chia_dialect.rs lines 56-76
const DISABLE_OP = 0x200;

pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)          // ← blocks op_modpow
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

The dispatch arm for opcode 60 in `ChiaDialect::op` checks this flag before routing:

```rust
// src/chia_dialect.rs lines 239-244
60 => {
    if flags.contains(ClvmFlags::DISABLE_OP) {
        return Err(EvalErr::Unimplemented(o))?;
    }
    op_modpow
}
```

Without `DISABLE_OP` (consensus mode), opcode 60 resolves to `op_modpow`. With `DISABLE_OP` (mempool mode), it returns `EvalErr::Unimplemented`. No other flag or softfork guard governs `op_modpow`; it is a first-class consensus operator with its own cost constants (`MODPOW_BASE_COST = 17000`, `MODPOW_COST_PER_BYTE_BASE_VALUE = 38`, etc.) and is registered by name in `f_table.rs`.

Unlike every other conditionally-enabled operator in the file — `ENABLE_KECCAK_OPS_OUTSIDE_GUARD`, `ENABLE_SHA256_TREE`, `RELAXED_BLS` — all of which carry the comment *"Hard-fork; enable only when it activates"* — `DISABLE_OP` carries no such annotation and no explanation for why a live consensus operator should be suppressed in mempool mode.

The Python-facing entry point `run_serialized_chia_program` in `wheel/src/api.rs` accepts a raw `u32 flags` from the caller, constructs `ChiaDialect::new(flags)`, and passes it directly to `run_program`. Any caller passing `MEMPOOL_MODE` (or any flags word with bit `0x200` set) will silently disable `op_modpow` for that execution.

---

### Impact Explanation

**Impact: High.**

A coin whose puzzle uses `op_modpow` (opcode 60) is valid on-chain — full nodes in consensus mode will accept and execute it. However, every node running mempool validation with `MEMPOOL_MODE` (the standard production configuration) will reject the spend with `EvalErr::Unimplemented`. Because Chia transactions reach the chain through the mempool, the coin is effectively frozen: the owner cannot spend it through any normal path. This is the direct analog to the original report's finding — a flag controlled by the infrastructure layer silently prevents users from exercising a right (spending a coin / withdrawing funds) that the protocol itself guarantees.

---

### Likelihood Explanation

**Likelihood: Medium.**

`op_modpow` is a deployed, tested operator with calibrated costs and named registration in `f_table.rs`. Any puzzle author who uses it — for RSA-based puzzles, VDF verification helpers, or modular-exponentiation-based commitments — will find their coins unspendable via the mempool. The flag is not gated behind a feature flag or build-time option; it is compiled into every production binary as part of `MEMPOOL_MODE`.

---

### Recommendation

Either:

1. **Remove `DISABLE_OP` from `MEMPOOL_MODE`** if `op_modpow` is a live consensus operator that should be reachable in mempool mode (the correct fix if the flag was added by mistake or is stale).
2. **Add a matching consensus-mode guard** that also disables opcode 60 in block validation, and document the intended activation epoch — mirroring the pattern used for `ENABLE_KECCAK_OPS_OUTSIDE_GUARD` and `ENABLE_SHA256_TREE`.
3. At minimum, **add an explanatory comment** to `DISABLE_OP` and to its inclusion in `MEMPOOL_MODE` so the intended semantics are auditable.

---

### Proof of Concept

```
# Construct a minimal puzzle that calls op_modpow (opcode 60 = 0x3c)
# CLVM: (modpow (q . 2) (q . 10) (q . 1000))
# Serialized bytes include opcode 0x3c

1. Serialize the puzzle using node_to_bytes / the Python clvm_rs wheel.
2. Call run_serialized_chia_program(puzzle_bytes, args_bytes, max_cost,
       flags=MEMPOOL_MODE)   # 0x0017 = NO_UNKNOWN_OPS|LIMIT_HEAP|DISABLE_OP|CANONICAL_INTS|LIMIT_SOFTFORK
3. Observe: EvalErr::Unimplemented returned for opcode 60.
4. Call run_serialized_chia_program(puzzle_bytes, args