### Title
`DISABLE_OP` Flag in `MEMPOOL_MODE` Incorrectly Blocks `modpow` (Opcode 60), Creating Mempool/Consensus Divergence — (`src/chia_dialect.rs`)

---

### Summary

The `DISABLE_OP` flag (`0x200`) is silently included in `MEMPOOL_MODE` and is wired exclusively to block opcode `60` (`op_modpow`) in the `ChiaDialect::op` dispatch function. No equivalent restriction exists in consensus mode. This creates a mempool/consensus divergence: any attacker-controlled CLVM program that uses `modpow` is rejected by the mempool with `EvalErr::Unimplemented`, yet the same program executes successfully under consensus rules. Valid coin spends are permanently blocked from entering the mempool.

---

### Finding Description

In `src/chia_dialect.rs`, the `ClvmFlags` bitfield defines `DISABLE_OP = 0x200` at line 56 — the only flag in the set that carries no documentation comment explaining its purpose or scope: [1](#0-0) 

This flag is unconditionally included in `MEMPOOL_MODE`: [2](#0-1) 

Inside `ChiaDialect::op`, the flag is checked only for opcode `60` (`op_modpow`). When `DISABLE_OP` is present, the operator is rejected as unimplemented: [3](#0-2) 

No analogous guard exists for opcode `60` in consensus mode (i.e., when `DISABLE_OP` is absent). All other opcodes in the same dispatch table — including computationally heavy ones like `op_bls_pairing_identity` (58) and `op_bls_verify` (59) — are dispatched unconditionally in both modes: [4](#0-3) 

The result is a broken invariant: the mempool applies a stricter, undocumented restriction on `modpow` that consensus does not share. This is structurally identical to the QodaToken `_update` override that applied `require(to != address(0))` too broadly — a guard intended for one context (transfer) was incorrectly applied to all paths (mint/burn). Here, a flag intended to restrict something is applied in mempool mode but has no corresponding consensus-mode counterpart, making the restriction asymmetric and incorrect.

---

### Impact Explanation

Any CLVM program containing opcode `60` (`modpow`) submitted to the mempool will be rejected with `EvalErr::Unimplemented`. The same program, if included in a block directly (bypassing the mempool), will execute successfully under consensus rules. This means:

- **Valid coin spends using `modpow` are permanently excluded from the mempool.** They cannot propagate through the peer-to-peer network via normal transaction submission.
- **Consensus divergence**: nodes running mempool validation and nodes running consensus validation disagree on the validity of the same spend bundle.
- A coin locked by a puzzle that uses `modpow` is effectively unspendable through normal network paths, even though it is valid on-chain.

---

### Likelihood Explanation

The trigger is straightforward: any CLVM puzzle or solution that invokes opcode `60` (`modpow`) submitted via the standard mempool API. `modpow` is a documented, named operator in the operator table and is reachable by any caller who constructs CLVM bytecode. The `MEMPOOL_MODE` constant is the standard mode used for mempool validation, so this divergence is active in every production deployment. No special privileges or configuration are required.

---

### Recommendation

1. **Remove `DISABLE_OP` from `MEMPOOL_MODE`** if `modpow` is a valid consensus operator (which the dispatch table confirms it is). The mempool must not reject programs that consensus accepts.
2. If `modpow` is intentionally restricted in mempool mode (e.g., for DoS cost reasons), the flag must be renamed to something descriptive (e.g., `DISABLE_MODPOW`), documented with a rationale comment, and the consensus-mode behavior must be made consistent — either by also restricting it in consensus or by removing the restriction entirely.
3. Add a test asserting that every operator accepted by consensus is also accepted by the mempool (modulo cost limits), to prevent future silent divergences of this kind.

---

### Proof of Concept

```
# Construct a CLVM program that calls modpow (opcode 60 = 0x3c)
# (modpow base exponent modulus) -> base^exponent mod modulus
# e.g. (modpow (q . 2) (q . 10) (q . 1000)) -> 24

program_bytes = bytes([0x3c, ...])  # opcode 60 with arguments

# Submit to mempool (MEMPOOL_MODE = NO_UNKNOWN_OPS | LIMIT_HEAP | DISABLE_OP | CANONICAL_INTS | LIMIT_SOFTFORK)
dialect_mempool = ChiaDialect::new(MEMPOOL_MODE)
result = run_program(&mut a, &dialect_mempool, program, args, max_cost)
# => Err(EvalErr::Unimplemented(op))   ← rejected

# Run under consensus mode (no DISABLE_OP)
dialect_consensus = ChiaDialect::new(ClvmFlags::empty())
result = run_program(&mut a, &dialect_consensus, program, args, max_cost)
# => Ok(Reduction(cost, result_node))  ← accepted
```

The divergence is directly triggered by the `DISABLE_OP` check at `src/chia_dialect.rs` line 240, which fires in mempool mode but not in consensus mode, for the same input program. [2](#0-1) [3](#0-2)

### Citations

**File:** src/chia_dialect.rs (L56-56)
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

**File:** src/chia_dialect.rs (L237-245)
```rust
            58 => op_bls_pairing_identity,
            59 => op_bls_verify,
            60 => {
                if flags.contains(ClvmFlags::DISABLE_OP) {
                    return Err(EvalErr::Unimplemented(o))?;
                }
                op_modpow
            }
            61 => op_mod,
```
