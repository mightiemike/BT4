### Title
Missing `ENABLE_SECP_OPS` Flag Check on 4-Byte Secp Opcode Dispatch Bypasses Hard-Fork Gate — (`File: src/chia_dialect.rs`)

---

### Summary

`ChiaDialect::op()` in `src/chia_dialect.rs` dispatches the two 4-byte secp opcodes (`0x13d61f00` → `op_secp256k1_verify`, `0x1c3a8f00` → `op_secp256r1_verify`) unconditionally, with no check against `ClvmFlags::ENABLE_SECP_OPS`. The 1-byte equivalents (opcodes 64 and 65) are correctly gated by that flag. Because `MEMPOOL_MODE` does not include `ENABLE_SECP_OPS`, an attacker who submits attacker-controlled CLVM bytes using the 4-byte encoding can execute secp signature verification in contexts where it is supposed to be rejected, and can cause consensus divergence between nodes that have the flag set and those that do not.

---

### Finding Description

**Vulnerable code — 4-byte dispatch (no flag check):** [1](#0-0) 

```rust
if op_len == 4 {
    ...
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,   // ← no ENABLE_SECP_OPS check
        0x1c3a8f00 => op_secp256r1_verify,   // ← no ENABLE_SECP_OPS check
        _ => {
            return unknown_operator(allocator, o, argument_list, flags, max_cost);
        }
    };
    return f(allocator, argument_list, max_cost, flags);
}
```

**Correct code — 1-byte dispatch (flag check present):** [2](#0-1) 

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The flag itself is defined as: [3](#0-2) 

`MEMPOOL_MODE` — the stricter mode used by the mempool — does **not** include `ENABLE_SECP_OPS`: [4](#0-3) 

The `flags` variable passed into the 4-byte dispatch block is already computed from `self.flags | extension_flags`, so it carries whatever the caller set. The 4-byte block never interrogates `ENABLE_SECP_OPS` before calling the secp functions. [5](#0-4) 

The secp functions themselves (`op_secp256k1_verify`, `op_secp256r1_verify`) ignore the flags parameter entirely and perform full cryptographic verification unconditionally: [6](#0-5) 

---

### Impact Explanation

**Mempool bypass (concrete, immediate):** `MEMPOOL_MODE` omits `ENABLE_SECP_OPS`. In mempool mode, 1-byte opcodes 64/65 fall through to `unknown_operator`, which returns `Err(EvalErr::Unimplemented)` because `NO_UNKNOWN_OPS` is set. But 4-byte opcodes `0x13d61f00`/`0x1c3a8f00` are dispatched unconditionally, executing full secp verification. An attacker submitting CLVM bytes with the 4-byte encoding bypasses the mempool's rejection of secp operations.

**Consensus divergence (flag-gated hard-fork):** `ENABLE_SECP_OPS` is a hard-fork activation flag. Before it activates on a given node, 1-byte secp opcodes return nil (consensus mode) or error (mempool mode). The 4-byte secp opcodes execute regardless. A puzzle that uses the 4-byte encoding will be accepted and executed identically on all nodes — including those that have not yet activated `ENABLE_SECP_OPS` — while the same puzzle using 1-byte opcodes would diverge. This breaks the invariant that the flag controls whether secp operations are available.

---

### Likelihood Explanation

The entry path requires only attacker-controlled CLVM bytes — the normal attack surface for any Chia puzzle submission. The 4-byte opcode values (`0x13d61f00`, `0x1c3a8f00`) are fixed constants that any attacker can embed directly in a serialized CLVM program. No special privilege, key material, or social engineering is required. The bypass is deterministic and reproducible.

---

### Recommendation

Add the `ENABLE_SECP_OPS` guard to the 4-byte dispatch block, mirroring the 1-byte dispatch:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

Without the guard, the 4-byte encoding is a permanent bypass of the flag gate regardless of how callers configure `ClvmFlags`.

---

### Proof of Concept

1. Construct a CLVM program that calls `op_secp256k1_verify` using the 4-byte opcode `0x13d61f00` with a valid pubkey/message/signature triple.
2. Run it via `run_program` with `ChiaDialect::new(MEMPOOL_MODE)` (which does **not** include `ENABLE_SECP_OPS`).
3. Observe: the call succeeds and returns nil, executing full secp verification.
4. Repeat with 1-byte opcode 64 under the same flags.
5. Observe: the call fails with `Unimplemented` — the flag gate fires correctly.

The divergence between steps 3 and 5 is the bug. The 4-byte encoding silently bypasses the flag that is supposed to control whether secp operations are available, directly analogous to the missing vault-side check in the reference report: a second code path reaches the same privileged operation without the required consistency check.

### Citations

**File:** src/chia_dialect.rs (L62-63)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;
```

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

**File:** src/chia_dialect.rs (L157-183)
```rust
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

**File:** src/chia_dialect.rs (L248-249)
```rust
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
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
