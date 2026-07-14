### Title
`op_modpow` (Opcode 60) Completely Blocked in Mempool Mode by `DISABLE_OP` Flag Creates Consensus/Mempool Divergence — (File: `src/chia_dialect.rs`)

---

### Summary

The `DISABLE_OP` flag, included in the public `MEMPOOL_MODE` constant, completely disables `op_modpow` (opcode 60) during mempool validation. Unlike every other "not-yet-activated" operator in the codebase (opcodes 62, 63, 64, 65), which are gated behind specific opt-in enable flags, `op_modpow` sits unconditionally in the main operator dispatch table and is fully reachable in consensus mode. Any coin puzzle that uses `op_modpow` is therefore rejected by the mempool but accepted by consensus nodes — a concrete mempool/consensus divergence that permanently locks those coins.

---

### Finding Description

In `src/chia_dialect.rs`, the operator dispatch for opcode 60 reads:

```rust
60 => {
    if flags.contains(ClvmFlags::DISABLE_OP) {
        return Err(EvalErr::Unimplemented(o))?;
    }
    op_modpow
}
``` [1](#0-0) 

`DISABLE_OP` (bit `0x200`) is unconditionally included in the public `MEMPOOL_MODE` constant:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
``` [2](#0-1) 

Every other operator that is not yet hardforked follows a consistent pattern: it requires a specific opt-in enable flag before it is reachable at all. For example:

- Opcode 62 (`keccak256`): requires `ENABLE_KECCAK_OPS_OUTSIDE_GUARD`
- Opcode 63 (`sha256_tree`): requires `ENABLE_SHA256_TREE`
- Opcodes 64–65 (`secp256k1_verify`, `secp256r1_verify`): require `ENABLE_SECP_OPS` [3](#0-2) 

`op_modpow` does not follow this pattern. It is present in the main dispatch table with no activation guard, and it is listed as a GC candidate (opcode 60 appears in the `gc_candidate` match arm), confirming it is treated as a fully hardforked operator in consensus mode: [4](#0-3) 

The result is a split: consensus mode (no `DISABLE_OP`) executes `op_modpow` normally; mempool mode returns `EvalErr::Unimplemented`. Any coin whose puzzle contains opcode 60 is valid on-chain but permanently unspendable through the mempool.

The `DISABLE_OP` flag also restricts `op_div`, `op_divmod`, and `op_mod` to 2048-byte operands in mempool mode, which is a reasonable anti-DoS measure. But the complete rejection of `op_modpow` — rather than a size restriction — is inconsistent and constitutes an overly broad gate, directly analogous to the `onlyWhenVaultIsOn` modifier in M-36 that blocked `withdrawalRequest` even when it should have been permitted. [5](#0-4) 

---

### Impact Explanation

A coin puzzle containing opcode 60 (`modpow`) is valid under consensus rules (full node accepts the spend) but is rejected by every mempool node with `EvalErr::Unimplemented`. Because Chia transactions reach blocks through the mempool, the spend can never be propagated. The coin is permanently locked — its value is inaccessible — for as long as `DISABLE_OP` remains in `MEMPOOL_MODE`. This is a direct consensus/mempool divergence with asset-locking consequences.

---

### Likelihood Explanation

The entry path is fully attacker-controlled: an attacker submits CLVM bytes containing opcode 60 to any node running mempool validation with `MEMPOOL_MODE`. No privileged access, social engineering, or dependency compromise is required. Any user who legitimately constructs a puzzle using `op_modpow` (a documented, hardforked operator) will also trigger this divergence unintentionally.

---

### Recommendation

Two consistent remedies exist:

1. **If `op_modpow` is fully hardforked**: remove `DISABLE_OP` from `MEMPOOL_MODE`, or replace the complete rejection of opcode 60 with a size-based restriction (matching the treatment of `op_div`/`op_divmod`/`op_mod`) so that mempool and consensus agree on validity.

2. **If `op_modpow` is not yet activated**: gate it behind a dedicated opt-in flag (e.g., `ENABLE_MODPOW`) following the same pattern used for opcodes 62, 63, 64, and 65, so that the operator is unreachable in both mempool and consensus mode until the hardfork activates.

The current design — where the operator is reachable in consensus but silently killed in mempool by a generic, undocumented flag — is the root cause of the divergence.

---

### Proof of Concept

```
Puzzle: (modpow (q . 3) (q . 10) (q . 7))   ; opcode 60, computes 3^10 mod 7 = 4

Consensus mode (ClvmFlags::empty()):
  → op_modpow executes → Ok(Reduction(cost, atom(4)))

Mempool mode (MEMPOOL_MODE, includes DISABLE_OP):
  → opcode 60 dispatch hits DISABLE_OP check
  → Err(EvalErr::Unimplemented(op_node))
  → spend rejected by every mempool node

Result: coin is valid on-chain but permanently unspendable through the mempool.
``` [1](#0-0) [2](#0-1)

### Citations

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L127-133)
```rust
            NodeVisitor::U32(
                2 | 7 | 9 | 10 | 11 | 13 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 | 24 | 25 | 26
                | 27 | 29 | 30 | 32 | 33 | 34 | 48 | 49 | 50 | 51 | 56 | 58 | 59 | 60 | 61 | 62
                | 63,
            ) => true,
            _ => false,
        }
```

**File:** src/chia_dialect.rs (L239-244)
```rust
            60 => {
                if flags.contains(ClvmFlags::DISABLE_OP) {
                    return Err(EvalErr::Unimplemented(o))?;
                }
                op_modpow
            }
```

**File:** src/chia_dialect.rs (L246-252)
```rust
            62 if flags.contains(ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD) => op_keccak256,
            63 if flags.contains(ClvmFlags::ENABLE_SHA256_TREE) => op_sha256_tree,
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
            _ => {
                return unknown_operator(allocator, o, argument_list, flags, max_cost);
            }
```

**File:** src/more_ops.rs (L665-667)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
```
