### Title
Missing `ENABLE_SECP_OPS` Flag Check for 4-Byte Secp Opcodes Allows Pre-Activation Secp Verification — (`File: src/chia_dialect.rs`)

---

### Summary

The `ChiaDialect::op()` function in `src/chia_dialect.rs` dispatches the 4-byte secp opcodes `0x13d61f00` (`secp256k1_verify`) and `0x1c3a8f00` (`secp256r1_verify`) directly to their verification functions **without checking `ClvmFlags::ENABLE_SECP_OPS`**, while the 1-byte alias opcodes 64 and 65 for the same operations **do** check this flag. This is a direct analog to the Venus `preLiquidateHook` missing membership check: a gate condition is enforced on one code path but silently absent on a functionally equivalent path, allowing the gated action to proceed when it should be blocked.

---

### Finding Description

In `src/chia_dialect.rs`, the `op()` function has two separate dispatch paths for secp operations:

**4-byte opcode path (lines 157–183) — no flag check:**
```rust
if op_len == 4 {
    let b = allocator.atom(o);
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,   // dispatched unconditionally
        0x1c3a8f00 => op_secp256r1_verify,   // dispatched unconditionally
        _ => {
            return unknown_operator(allocator, o, argument_list, flags, max_cost);
        }
    };
    return f(allocator, argument_list, max_cost, flags);
}
```

**1-byte alias path (lines 248–249) — flag check present:**
```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

`ENABLE_SECP_OPS` is never injected by any `OperatorSet` variant in the `extension` merge (lines 144–154), so it can only come from the dialect's own `self.flags`. When it is absent (e.g., pre-fork consensus mode or any caller that omits it), opcode 64 falls through to `unknown_operator` — a no-op with cost — while `0x13d61f00` still executes real secp256k1 signature verification. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

The concrete divergence is:

| Condition | Opcode 64 result | `0x13d61f00` result |
|---|---|---|
| `ENABLE_SECP_OPS` **not set** | no-op (`op_unknown`, returns nil) | real `secp256k1_verify` (can return `Secp256Failed`) |
| `ENABLE_SECP_OPS` **set** | real `secp256k1_verify` | real `secp256k1_verify` |

An attacker-controlled CLVM program that uses `0x13d61f00` with an invalid or forged signature will raise `EvalErr::Secp256Failed` even in a pre-fork environment where `ENABLE_SECP_OPS` is absent and opcode 64 would be a harmless no-op. Conversely, a program using `0x13d61f00` with a valid signature silently performs real cryptographic verification and succeeds — bypassing the hard-fork gate entirely. This means:

1. **Pre-activation secp execution**: Any caller (mempool, generator runner, Python wheel) that omits `ENABLE_SECP_OPS` still executes full secp verification for 4-byte opcodes, defeating the purpose of the flag as a hard-fork activation gate.
2. **Operator-set semantic divergence**: Two programs that are semantically identical (same secp check, different opcode encoding) produce different outcomes under the same dialect flags — one succeeds as a no-op, the other fails with a cryptographic error. This is an API-equivalence break exploitable by any party who can choose the opcode encoding.
3. **Mempool inconsistency**: `MEMPOOL_MODE` does not include `ENABLE_SECP_OPS`. In mempool mode, opcode 64 is rejected as `Unimplemented` (via `NO_UNKNOWN_OPS`), but `0x13d61f00` still executes real secp verification — a different policy for the same logical operation. [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation

The entry path is fully attacker-controlled: any CLVM byte sequence submitted to `run_program` (via the Rust API, the Python wheel's `run_serialized_chia_program`, or the CLI tool) can encode the 4-byte opcode `0x13d61f00` or `0x1c3a8f00` as the operator atom. No special privilege is required. The 4-byte form is a valid, parseable atom that passes the `op_len == 4` branch unconditionally. The inconsistency is therefore reachable by any external program input. [6](#0-5) 

---

### Recommendation

Add the `ENABLE_SECP_OPS` flag check to the 4-byte secp opcode dispatch, mirroring the guard already present on the 1-byte aliases:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

This ensures both opcode encodings of the same operation are governed by the same activation gate, eliminating the semantic divergence and restoring the hard-fork flag's intended role as a uniform guard. [7](#0-6) 

---

### Proof of Concept

```
# Dialect: ChiaDialect with flags = ClvmFlags::empty() (no ENABLE_SECP_OPS)
# Program A (1-byte opcode 64): (64 pubkey msg bad_sig)
#   -> unknown_operator -> op_unknown -> returns nil, cost N   [succeeds as no-op]

# Program B (4-byte opcode 0x13d61f00): (0x13d61f00 pubkey msg bad_sig)
#   -> op_secp256k1_verify -> Err(Secp256Failed)               [fails with crypto error]

# Same logical operation, same dialect flags, opposite outcomes.
# The gate ENABLE_SECP_OPS is enforced on path A but absent on path B.
``` [8](#0-7) [9](#0-8)

### Citations

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L144-154)
```rust
        let flags = self.flags
            | match extension {
                // This is the default set of operators, so no special flags need to be added.
                OperatorSet::Default => ClvmFlags::empty(),

                // Since BLS has been hardforked in universally, this has no effect.
                OperatorSet::Bls => ClvmFlags::empty(),

                // Keccak is allowed as if it were a default operator, inside of the softfork guard.
                OperatorSet::Keccak => ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD,
            };
```

**File:** src/chia_dialect.rs (L156-183)
```rust
        let op_len = allocator.atom_len(o);
        if op_len == 4 {
            // these are unknown operators with assigned cost
            // the formula is:
            // +---+---+---+------------+
            // | multiplier|XX | XXXXXX |
            // +---+---+---+---+--------+
            //  ^           ^    ^
            //  |           |    + 6 bits ignored when computing cost
            // cost         |
            // (3 bytes)    + 2 bits
            //                cost_function

            let b = allocator.atom(o);
            let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());

            // the secp operators have a fixed cost of 1850000 and 1300000,
            // which makes the multiplier 0x1c3a8f and 0x0cf84f (there is an
            // implied +1) and cost function 0
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

**File:** src/secp_ops.rs (L61-103)
```rust
pub fn op_secp256k1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let cost = SECP256K1_VERIFY_COST;
    check_cost(cost, max_cost)?;

    let [pubkey, msg, sig] = get_args::<3>(a, input, "secp256k1_verify")?;

    // first argument is sec1 encoded pubkey
    let pubkey = atom(a, pubkey, "secp256k1_verify pubkey")?;
    let verifier = K1VerifyingKey::from_sec1_bytes(pubkey.as_ref()).map_err(|_| {
        EvalErr::InvalidOpArg(input, "secp256k1_verify: pubkey is not valid".to_string())
    })?;

    // second arg is message
    let msg = atom(a, msg, "secp256k1_verify msg")?;
    if msg.as_ref().len() != 32 {
        Err(EvalErr::InvalidOpArg(
            input,
            "secp256k1_verify: message digest is not 32 bytes".to_string(),
        ))?;
    }

    // third arg is a fixed-size signature
    let sig = atom(a, sig, "secp256k1_verify sig")?;
    let sig = K1Signature::from_slice(sig.as_ref()).map_err(|_| {
        EvalErr::InvalidOpArg(
            input,
            "secp256k1_verify: signature is not valid".to_string(),
        )
    })?;

    // verify signature
    let result = verifier.verify_prehash(msg.as_ref(), &sig);

    if result.is_err() {
        Err(EvalErr::Secp256Failed(input))?
    } else {
        Ok(Reduction(cost, a.nil()))
    }
```
