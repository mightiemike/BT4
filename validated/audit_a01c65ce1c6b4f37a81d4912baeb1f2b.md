### Title
`op_modpow` (opcode 60) Hardcoded Disabled in `MEMPOOL_MODE` via `DISABLE_OP` Flag, Causing Consensus/Mempool Divergence - (File: `src/chia_dialect.rs`)

---

### Summary

The `DISABLE_OP` flag (`0x200`) is unconditionally included in `MEMPOOL_MODE` and hardcoded to reject opcode 60 (`op_modpow`) with `EvalErr::Unimplemented`. Because `op_modpow` is a fully wired, non-softforked operator in the consensus operator table, any CLVM program using it is valid in block-validation mode but silently rejected by every mempool node. This is a direct flag/operator wiring error that creates a permanent consensus–mempool divergence.

---

### Finding Description

In `src/chia_dialect.rs`, the `ClvmFlags` bitfield defines a flag with no descriptive comment:

```rust
const DISABLE_OP = 0x200;
``` [1](#0-0) 

This flag is unconditionally composed into `MEMPOOL_MODE`:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)          // ← hardcoded
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
``` [2](#0-1) 

Inside `ChiaDialect::op`, opcode 60 is the only operator that checks this flag and hard-fails:

```rust
60 => {
    if flags.contains(ClvmFlags::DISABLE_OP) {
        return Err(EvalErr::Unimplemented(o))?;
    }
    op_modpow
}
``` [3](#0-2) 

Every other operator in the same match arm — including all BLS operators (opcodes 48–59), `op_mod` (61), `op_keccak256` (62), `op_sha256_tree` (63), and the secp operators (64–65) — is either unconditionally dispatched or gated by a positive feature flag (`ENABLE_KECCAK_OPS_OUTSIDE_GUARD`, `ENABLE_SHA256_TREE`, `ENABLE_SECP_OPS`). Only `op_modpow` uses a *negative* kill-switch that is permanently active in mempool mode. [4](#0-3) 

`op_modpow` is not behind a softfork guard; it is a first-class entry in the consensus operator table alongside `op_mod`, `op_add`, and the BLS suite. [5](#0-4) 

---

### Impact Explanation

Any CLVM puzzle or spend bundle that invokes opcode 60 (`op_modpow`) is:

- **Accepted** by a full node running in consensus/block-validation mode (no `DISABLE_OP` flag set).
- **Rejected** with `EvalErr::Unimplemented` by every mempool node running `MEMPOOL_MODE`.

Because all transactions must pass mempool validation before being relayed and included in blocks, `op_modpow` is effectively non-functional for any on-chain use. A coin locked with a puzzle that calls `op_modpow` cannot be spent through the normal transaction pipeline. The corrupted result is a hard `EvalErr::Unimplemented` on a valid, fully-implemented operator — a concrete, identifiable wrong outcome.

---

### Likelihood Explanation

The likelihood is **high**. `MEMPOOL_MODE` is the standard flag set used by the Chia full node for all incoming transactions. Any caller — including the Python wheel API — that passes `MEMPOOL_MODE` to `ChiaDialect::new` will trigger this rejection for every program containing opcode 60. The entry path requires only attacker-controlled (or user-controlled) CLVM bytes that include a call to opcode 60; no special privileges or configuration are needed. [6](#0-5) 

---

### Recommendation

Remove `ClvmFlags::DISABLE_OP` from `MEMPOOL_MODE`. If `op_modpow` is intentionally being staged for a future hard-fork activation, it should be gated by a positive feature flag (analogous to `ENABLE_KECCAK_OPS_OUTSIDE_GUARD`) that is absent from both mempool and consensus modes until the fork activates — not by a kill-switch that is active only in mempool mode, which inverts the expected consensus/mempool relationship. [2](#0-1) 

---

### Proof of Concept

1. Construct a minimal CLVM program invoking opcode 60: `(60 base exp mod)` — e.g., `(modpow 2 10 1000)`.
2. Run it under `ChiaDialect::new(ClvmFlags::empty())` (consensus mode) → succeeds, returns `24`.
3. Run the identical program under `ChiaDialect::new(MEMPOOL_MODE)` → returns `EvalErr::Unimplemented(op_node)`.
4. The divergence is triggered purely by the `DISABLE_OP` bit in `MEMPOOL_MODE`; removing it from the constant restores correct behaviour. [3](#0-2) [2](#0-1)

### Citations

**File:** src/chia_dialect.rs (L56-57)
```rust
        const DISABLE_OP = 0x200;

```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L96-99)
```rust
impl ChiaDialect {
    pub fn new(flags: ClvmFlags) -> ChiaDialect {
        ChiaDialect { flags }
    }
```

**File:** src/chia_dialect.rs (L227-253)
```rust
            48 => op_coinid,
            49 => op_bls_g1_subtract,
            50 => op_bls_g1_multiply,
            51 => op_bls_g1_negate,
            52 => op_bls_g2_add,
            53 => op_bls_g2_subtract,
            54 => op_bls_g2_multiply,
            55 => op_bls_g2_negate,
            56 => op_bls_map_to_g1,
            57 => op_bls_map_to_g2,
            58 => op_bls_pairing_identity,
            59 => op_bls_verify,
            60 => {
                if flags.contains(ClvmFlags::DISABLE_OP) {
                    return Err(EvalErr::Unimplemented(o))?;
                }
                op_modpow
            }
            61 => op_mod,
            62 if flags.contains(ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD) => op_keccak256,
            63 if flags.contains(ClvmFlags::ENABLE_SHA256_TREE) => op_sha256_tree,
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
            _ => {
                return unknown_operator(allocator, o, argument_list, flags, max_cost);
            }
        };
```
