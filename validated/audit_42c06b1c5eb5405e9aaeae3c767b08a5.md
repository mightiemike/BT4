### Title
`op_keccak256` Intermediate Cost Check Uses Stale `byte_count`, Allowing Hashing Beyond Cost Budget - (`src/keccak256_ops.rs`)

### Summary

`op_keccak256` accumulates per-byte cost in a separate `byte_count` variable and only adds it to `cost` after the argument loop. The intermediate `check_cost` call inside the loop uses the **previous iteration's** `byte_count`, not the current argument's bytes. This means the cost guard is always one argument behind, and the last argument's bytes are never checked before hashing. An attacker-controlled CLVM program can force a node to hash an arbitrarily large blob before the cost limit is enforced, performing computation beyond what the budget allows.

### Finding Description

In `src/keccak256_ops.rs`, `op_keccak256` is structured as:

```rust
let mut byte_count: usize = 0;
while let Some((arg, rest)) = a.next(input) {
    input = rest;
    cost += KECCAK256_COST_PER_ARG;
    check_cost(
        cost + byte_count as Cost * KECCAK256_COST_PER_BYTE,  // stale byte_count
        max_cost,
    )?;
    let blob = atom(a, arg, "keccak256")?;
    byte_count += blob.as_ref().len();   // updated AFTER the check
    hasher.update(blob);                 // hashing happens unconditionally
}
cost += byte_count as Cost * KECCAK256_COST_PER_BYTE;
``` [1](#0-0) 

On iteration N, `check_cost` is called with the byte count from iterations 1..N-1. The current argument's bytes are added to `byte_count` only after the check, and `hasher.update(blob)` executes unconditionally. The final per-byte cost is only committed to `cost` after the loop exits.

Compare to `op_sha256`, which correctly reads the blob, adds its byte cost to `cost`, calls `check_cost`, and only then calls `hasher.update`:

```rust
let blob = atom(a, arg, "sha256")?;
cost += blob.as_ref().len() as Cost * SHA256_COST_PER_BYTE;
check_cost(cost, max_cost)?;
hasher.update(blob);
``` [2](#0-1) 

The cost constants involved are `KECCAK256_BASE_COST = 50`, `KECCAK256_COST_PER_ARG = 160`, `KECCAK256_COST_PER_BYTE = 2`. [3](#0-2) 

### Impact Explanation

An attacker submits a CLVM program calling `keccak256` with a single large atom (e.g., 1 MB). With `max_cost = 1000`:

- Iteration 1: `check_cost(50 + 160 + 0 * 2, 1000)` → **passes** (byte_count = 0, stale)
- `hasher.update(1_MB_blob)` executes — expensive Keccak256 hashing of 1 MB occurs
- After loop: `cost = 210 + 1_000_000 * 2 = 2_000_210`
- `run_program` rejects with `CostExceeded`

The hashing work is done before the cost limit is enforced. With `op_sha256`, the same scenario would fail at `check_cost` before `hasher.update` is reached. The attacker can force nodes to perform unbounded Keccak256 hashing work on each rejected transaction, constituting a denial-of-service amplification: the attacker pays only the cost of submitting a transaction, while the node performs O(blob_size) hashing.

The final returned cost is correct and the program is rejected, but the computation has already occurred. This is the direct analog of the original report's "undercharged execution": the cost check does not account for the actual work performed.

### Likelihood Explanation

`op_keccak256` is reachable via two paths:

1. Inside the softfork guard with extension 1 (`OperatorSet::Keccak`), available to any CLVM program on-chain.
2. Outside the guard when `ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD` is set (hard-fork flag). [4](#0-3) [5](#0-4) 

The attacker-controlled entry path is: craft a CLVM program with `(keccak256 <large_atom>)` inside a softfork guard specifying a low cost. The softfork guard's cost check (`expected_cost`) is validated on exit, but `op_keccak256` runs and hashes the blob before that exit check fires. [6](#0-5) 

The attack requires no special privileges, only the ability to submit a CLVM program.

### Recommendation

Follow the pattern of `op_sha256`: read the blob, add its byte cost to `cost`, call `check_cost`, then hash:

```rust
while let Some((arg, rest)) = a.next(input) {
    input = rest;
    cost += KECCAK256_COST_PER_ARG;
    let blob = atom(a, arg, "keccak256")?;
    cost += blob.as_ref().len() as Cost * KECCAK256_COST_PER_BYTE;
    check_cost(cost, max_cost)?;
    hasher.update(blob);
}
// remove the deferred cost addition after the loop
new_atom_and_cost(a, cost, &hasher.finalize())
```

This ensures the cost check gates the hashing work, matching the behavior of every other multi-argument hash operator in the codebase.

### Proof of Concept

```
; CLVM program (inside softfork guard, extension 1):
; (softfork (q . 210) (q . 1) (q . (keccak256 <1MB_atom>)) (q . ()))
;
; Trace:
;   op_keccak256 called with max_cost = 210 - 140 (GUARD_COST) - overhead
;   Iteration 1:
;     cost = 50 + 160 = 210
;     check_cost(210 + 0*2, max_cost) → passes (byte_count stale = 0)
;     blob = 1_000_000 bytes
;     hasher.update(1MB) ← 1MB Keccak256 hashing executes
;     byte_count = 1_000_000
;   After loop:
;     cost = 210 + 2_000_000 = 2_000_210
;   run_program: cost > max_cost → CostExceeded
;
; Result: node hashes 1MB per rejected transaction.
; With op_sha256 pattern, check_cost(210 + 2_000_000, max_cost) fires
; before hasher.update, preventing the work entirely.
```

### Citations

**File:** src/keccak256_ops.rs (L10-12)
```rust
const KECCAK256_BASE_COST: Cost = 50;
const KECCAK256_COST_PER_ARG: Cost = 160;
const KECCAK256_COST_PER_BYTE: Cost = 2;
```

**File:** src/keccak256_ops.rs (L22-36)
```rust
    let mut byte_count: usize = 0;
    let mut hasher = Keccak256::new();
    while let Some((arg, rest)) = a.next(input) {
        input = rest;
        cost += KECCAK256_COST_PER_ARG;
        check_cost(
            cost + byte_count as Cost * KECCAK256_COST_PER_BYTE,
            max_cost,
        )?;
        let blob = atom(a, arg, "keccak256")?;
        byte_count += blob.as_ref().len();
        hasher.update(blob);
    }
    cost += byte_count as Cost * KECCAK256_COST_PER_BYTE;
    new_atom_and_cost(a, cost, &hasher.finalize())
```

**File:** src/more_ops.rs (L400-407)
```rust
    while let Some((arg, rest)) = a.next(input) {
        input = rest;
        cost += SHA256_COST_PER_ARG;
        let blob = atom(a, arg, "sha256")?;
        cost += blob.as_ref().len() as Cost * SHA256_COST_PER_BYTE;
        check_cost(cost, max_cost)?;
        hasher.update(blob);
    }
```

**File:** src/chia_dialect.rs (L246-246)
```rust
            62 if flags.contains(ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD) => op_keccak256,
```

**File:** src/dialect.rs (L17-19)
```rust
    /// The keccak256 operator, which is only available inside the softfork guard.
    /// This uses softfork extension 1, which does not conflict with the BLS fork.
    Keccak,
```

**File:** src/run_program.rs (L453-469)
```rust
    fn exit_guard(&mut self, current_cost: Cost) -> Result<Cost> {
        // this is called when we are done executing a softfork program.
        // This is when we have to validate the cost
        let guard = self
            .softfork_stack
            .pop()
            .expect("internal error. exiting a softfork that's already been popped");

        if current_cost != guard.expected_cost {
            #[cfg(test)]
            println!(
                "actual cost: {} specified cost: {}",
                current_cost - guard.start_cost,
                guard.expected_cost - guard.start_cost
            );
            return Err(EvalErr::SoftforkCostMismatch);
        }
```
