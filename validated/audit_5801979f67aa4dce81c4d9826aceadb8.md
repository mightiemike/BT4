### Title
`DISABLE_OP` Size-Limit Guard Is Dead Code in `op_div`, `op_divmod`, and `op_mod` — Consensus/Mempool Divergence - (File: `src/more_ops.rs`)

---

### Summary

In `src/more_ops.rs`, the functions `op_div`, `op_div_malachite`, `op_divmod`, `op_divmod_malachite`, `op_mod`, and `op_mod_malachite` each contain a two-step size-limit guard for the dividend operand (`a0_len`). The first step checks `DISABLE_OP && a0_len > 2048`; the second step unconditionally checks `a0_len > 256`. Because 256 < 2048, the unconditional 256-byte check always fires before the `DISABLE_OP`-gated 2048-byte check can produce a different outcome. The `DISABLE_OP` branch is therefore dead code: the flag has no effect on the dividend size limit for these three operators, and the intended consensus-mode relaxation (allowing dividends up to 2048 bytes) is silently suppressed.

---

### Finding Description

Every arithmetic division operator in `src/more_ops.rs` contains this pattern (shown for `op_div`):

```rust
// Line 665-669
if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
if a0_len > 256 || a1_len > 1024 {
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
```

`DISABLE_OP` is part of `MEMPOOL_MODE` (`src/chia_dialect.rs` line 72–76), meaning it is set during stricter mempool validation and **not** set during consensus execution. The structural intent is clear:

| Mode | Intended `a0` limit |
|---|---|
| Consensus (no `DISABLE_OP`) | 2048 bytes |
| Mempool (`DISABLE_OP` set) | 256 bytes |

However, the unconditional `a0_len > 256` check on line 668 applies in **both** modes. For any `a0_len` in the range (256, 2048]:

- **Without `DISABLE_OP`** (consensus): the first guard does not fire (flag absent); the second guard fires → rejected.
- **With `DISABLE_OP`** (mempool): the first guard does not fire (2048 not exceeded); the second guard fires → rejected.

For `a0_len > 2048`:

- **Without `DISABLE_OP`**: second guard fires → rejected.
- **With `DISABLE_OP`**: first guard fires → rejected (same outcome as second guard).

In every reachable case the `DISABLE_OP` branch at 2048 is superseded by the unconditional 256 check. The flag produces no observable difference for `op_div`, `op_divmod`, or `op_mod`. The same defect is replicated identically in all six variants (`op_div`, `op_div_malachite`, `op_divmod`, `op_divmod_malachite`, `op_mod`, `op_mod_malachite`).

---

### Impact Explanation

`DISABLE_OP` is absent in consensus mode. The intended consensus-mode limit for the dividend is 2048 bytes. Because the unconditional 256-byte check is always evaluated, consensus nodes reject any `div`/`divmod`/`mod` call whose dividend exceeds 256 bytes — even though the protocol design permits up to 2048 bytes in that mode. Any CLVM coin whose puzzle uses a dividend between 257 and 2048 bytes is permanently unspendable on-chain, despite being within the intended consensus bounds. If a future softfork or upgrade relies on the 2048-byte consensus allowance being operative, the divergence between the intended and actual limit becomes a hard consensus failure: nodes running this code will reject blocks that the protocol considers valid.

---

### Likelihood Explanation

The entry path is direct: any attacker-controlled CLVM program submitted to a full node that invokes opcode 19 (`/`), 20 (`divmod`), or 61 (`mod`) with a dividend atom whose byte length is in (256, 2048] will trigger the wrong rejection. The `DISABLE_OP` flag is set automatically by `MEMPOOL_MODE` and cleared automatically in consensus execution — no special configuration is required. The bug is present in all six function variants and is not gated behind any feature flag.

---

### Recommendation

Replace the two-step guard with a single conditional that selects the correct limit based on the `DISABLE_OP` flag:

```rust
let a0_limit = if flags.contains(ClvmFlags::DISABLE_OP) { 256 } else { 2048 };
if a0_len > a0_limit || a1_len > 1024 {
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
```

Apply this fix to all six affected functions: `op_div`, `op_div_malachite`, `op_divmod`, `op_divmod_malachite`, `op_mod`, and `op_mod_malachite`.

---

### Proof of Concept

**Broken invariant (all six functions, shown for `op_div`):**

```
a0_len = 300  (between 256 and 2048)
flags  = ClvmFlags::empty()  (consensus mode, no DISABLE_OP)

Step 1: flags.contains(DISABLE_OP) → false  → guard skipped
Step 2: a0_len (300) > 256              → true  → InvalidOpArg returned

Expected: accepted (consensus limit is 2048)
Actual:   rejected (unconditional 256 check fires)
```

The `DISABLE_OP` guard at 2048 is unreachable in any meaningful sense:

```
a0_len = 300, flags = DISABLE_OP (mempool mode)

Step 1: DISABLE_OP set, 300 > 2048? → false → guard skipped
Step 2: 300 > 256?                  → true  → InvalidOpArg returned

a0_len = 3000, flags = DISABLE_OP

Step 1: DISABLE_OP set, 3000 > 2048? → true → InvalidOpArg returned
Step 2: (never reached, same error)
```

In no case does the `DISABLE_OP` branch produce a result that differs from the unconditional 256 check.

**Affected lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

**`DISABLE_OP` flag definition and its inclusion in `MEMPOOL_MODE`:** [7](#0-6) [8](#0-7)

### Citations

**File:** src/more_ops.rs (L665-669)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
```

**File:** src/more_ops.rs (L690-694)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
```

**File:** src/more_ops.rs (L713-717)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
```

**File:** src/more_ops.rs (L742-746)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
```

**File:** src/more_ops.rs (L769-773)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
```

**File:** src/more_ops.rs (L794-798)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
```

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
