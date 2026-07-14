### Title
Secp256k1/r1 Verify Operators (Opcodes 64/65) Silently Degrade to No-Op Unknown Operators When `ENABLE_SECP_OPS` Flag Is Absent, Bypassing Signature Verification in Consensus Mode — (`src/chia_dialect.rs`)

---

### Summary

`ChiaDialect::op()` dispatches 1-byte opcodes 64 and 65 to `op_secp256k1_verify` and `op_secp256r1_verify` only when the `ENABLE_SECP_OPS` flag is set. Without that flag, both opcodes silently fall through to `unknown_operator`, which in consensus mode (where `NO_UNKNOWN_OPS` is absent) returns `nil` with a well-defined cost — a no-op. The identical operations are simultaneously available unconditionally via 4-byte opcodes `0x13d61f00` and `0x1c3a8f00`. A coin puzzle that uses 1-byte opcode 64 or 65 for signature verification can therefore be spent without any valid signature whenever `ENABLE_SECP_OPS` is not active.

---

### Finding Description

In `src/chia_dialect.rs`, the operator dispatch table for 1-byte opcodes contains:

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
_ => {
    return unknown_operator(allocator, o, argument_list, flags, max_cost);
}
``` [1](#0-0) 

Without `ENABLE_SECP_OPS`, opcodes 64 and 65 fall into the `_` arm and call `unknown_operator`. In consensus mode — where `NO_UNKNOWN_OPS` is absent — `unknown_operator` delegates to `op_unknown`, which returns `nil` with a cost and no error. [2](#0-1) 

Simultaneously, the 4-byte opcode path dispatches the same functions **unconditionally**, with no flag guard:

```rust
let f = match opcode {
    0x13d61f00 => op_secp256k1_verify,
    0x1c3a8f00 => op_secp256r1_verify,
    _ => { return unknown_operator(...); }
};
return f(allocator, argument_list, max_cost, flags);
``` [3](#0-2) 

This creates a split: the 4-byte opcode path always enforces signature verification; the 1-byte opcode path enforces it only when `ENABLE_SECP_OPS` is set. A coin puzzle author who writes a puzzle using 1-byte opcode 64 (perhaps following documentation or examples that show the 4-byte form as equivalent) deploys a puzzle whose security guarantee is contingent on a runtime flag that may not be set.

`MEMPOOL_MODE` does not include `ENABLE_SECP_OPS`:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
``` [4](#0-3) 

In mempool mode, `NO_UNKNOWN_OPS` causes opcode 64 to be rejected outright. In consensus mode (without `NO_UNKNOWN_OPS` and without `ENABLE_SECP_OPS`), opcode 64 silently returns `nil` — the same return value as a successful signature verification — without performing any cryptographic check.

---

### Impact Explanation

A coin puzzle that uses 1-byte opcode 64 or 65 as its sole signature-verification gate can be spent by any party without a valid signature, provided the executing node runs consensus mode without `ENABLE_SECP_OPS`. The attacker supplies arbitrary (or empty) bytes for the pubkey, message, and signature arguments. The unknown-operator path returns `nil` unconditionally, satisfying the puzzle's condition. Funds protected by such a puzzle are fully unprotected.

This is a direct analog to the external report: just as the Chainlink FeedRegistry is assumed to exist in L2 but does not — causing the oracle to silently fail — opcode 64/65 is assumed to perform signature verification but silently becomes a no-op when the required flag is absent.

---

### Likelihood Explanation

The likelihood is medium. The `ENABLE_SECP_OPS` flag is a hard-fork activation flag. During any window between puzzle deployment and hard-fork activation, or on any node that has not yet set the flag, the vulnerability is live. The 4-byte opcode form (`0x13d61f00`) being unconditionally available creates a false sense of equivalence: a developer who sees both forms documented as "secp256k1_verify" may use the 1-byte form without understanding the flag dependency. The attacker-controlled entry path is straightforward: submit a spend bundle for a coin using opcode 64, with invalid or empty signature bytes, to a consensus-mode node without `ENABLE_SECP_OPS`.

---

### Recommendation

Either:
1. Remove the flag guard from opcodes 64 and 65 so they are always dispatched to the real secp functions (matching the 4-byte opcode behavior), or
2. Add a corresponding flag guard to the 4-byte opcode path so both paths have identical availability conditions, or
3. Document explicitly that coins using 1-byte opcodes 64/65 must not be deployed until `ENABLE_SECP_OPS` is universally active, and add a runtime assertion or error in consensus mode when the flag is absent and these opcodes are encountered.

---

### Proof of Concept

1. Construct a coin puzzle: `(secp256k1_verify pubkey msg sig)` using 1-byte opcode `\x40` (64).
2. Run `run_program` with `ChiaDialect::new(ClvmFlags::empty())` (consensus mode, no `ENABLE_SECP_OPS`, no `NO_UNKNOWN_OPS`).
3. Pass arbitrary bytes for pubkey, msg, sig.
4. Observe: `op` dispatches opcode 64 to `unknown_operator` → `op_unknown` → returns `Reduction(cost, nil)`.
5. The puzzle evaluates to `nil` (truthy in CLVM), the spend succeeds, no signature was verified.
6. Repeat with `ChiaDialect::new(ClvmFlags::ENABLE_SECP_OPS)`: the same inputs now reach `op_secp256k1_verify`, which rejects the invalid signature with `EvalErr::Secp256Failed`.

The corrupted result is the `NodePtr` returned by `op_unknown` — `nil` — which is indistinguishable from a successful `op_secp256k1_verify` return, causing the interpreter to treat an unsigned spend as valid. [5](#0-4) [6](#0-5)

### Citations

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L78-90)
```rust
fn unknown_operator(
    allocator: &mut Allocator,
    o: NodePtr,
    args: NodePtr,
    flags: ClvmFlags,
    max_cost: Cost,
) -> Response {
    if flags.contains(ClvmFlags::NO_UNKNOWN_OPS) {
        Err(EvalErr::Unimplemented(o))?
    } else {
        op_unknown(allocator, o, args, max_cost)
    }
}
```

**File:** src/chia_dialect.rs (L175-183)
```rust
            let f = match opcode {
                0x13d61f00 => op_secp256k1_verify,
                0x1c3a8f00 => op_secp256r1_verify,
                _ => {
                    return unknown_operator(allocator, o, argument_list, flags, max_cost);
                }
            };
            return f(allocator, argument_list, max_cost, flags);
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
