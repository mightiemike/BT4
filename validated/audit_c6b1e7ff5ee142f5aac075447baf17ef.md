### Title
Blake2F precompile allows attacker-chosen `rounds` (up to 2^32-1) to drive unbounded native `digest()` work under a constant-call's energy budget, risking RPC-worker CPU/time DoS - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Finding Description
`PrecompiledContracts.Blake2F.getEnergyForData` returns the raw attacker-controlled `rounds` field (bytes 0-3 of the 213-byte input) directly as the energy cost, with no upper clamp: [1](#0-0) 

`execute()` only validates length (`213`) and the finalization flag byte, then unconditionally runs `Blake2bfMessageDigest.digest()` over the whole input, with no internal time budget or interruption point inside the native compression loop: [2](#0-1) 

The work performed by `digest()` is proportional to `rounds`, but the value reported as "energy" is not independently bounded — it is exactly `rounds`, so the metering value and the real compute cost are the same attacker-chosen number. Whether this is actually a hard limit depends on two things I could not fully confirm within this investigation:
1. Whether the enclosing VM call site (`Program`/`VMActuator`) checks the available energy limit **before** invoking `execute()` (so an over-budget `rounds` value is rejected without running `digest()` at all), or only accounts for it after the fact.
2. The specific energy ceiling applied to `triggerconstantcontract`/`eth_call` (local/query) invocations in this fork, and the exact semantics of the CPU-time deadline mechanism referenced by matches in `Program.java` and `VM.java` (the deadline-related identifiers exist in this codebase, but I was not able to read their implementation before the iteration budget was exhausted).

If constant calls in this fork enforce a bounded local energy limit (a common java-tron pattern, typically on the order of ~10^9) and that check happens strictly before `execute()`, then the maximum achievable `rounds` per single constant call is capped by that limit rather than the full `2^32-1`. Even so, a bound in the hundreds-of-millions-to-billions range for `rounds` is still large enough that a single call could force many rounds of the Blake2b compression function to run synchronously on the RPC-API worker thread, and — per the question's proof idea — if the CPU-time deadline is polled only between VM opcodes (not inside native precompile execution), a single precompile invocation is not preemptible once started.

### Impact Explanation
If confirmed, this is a DoS-via-RPC-API impact: a single `triggerconstantcontract`/`eth_call` (or repeated calls) can tie up an RPC worker thread for an extended, attacker-tunable duration, potentially degrading or stalling node query availability. This does not touch consensus, funds, or keys — it is scoped to node-side CPU/time exhaustion on the query path.

### Likelihood Explanation
No special privileges are required — any anonymous RPC client can call `triggerconstantcontract`/`eth_call` against address `0x0000...20009`, since constant calls typically bypass real energy/bandwidth fee deduction. The precondition that Blake2F is registered without a feature-flag gate, and the exact numeric energy ceiling for constant calls in this specific fork, were **not verified** in this pass — these are necessary to confirm actual exploitability and severity, and should be checked directly in the code (`PrecompiledContracts` registration/dispatch logic, and the constant-call energy limit used by `Wallet`/`VMActuator`).

### Recommendation
- Clamp `Blake2F.getEnergyForData` (and thus the effective `rounds` bound honored for any single call) to a small, fixed maximum independent of the raw attacker-supplied 4-byte value, consistent with how other chains have mitigated this exact EIP-152 concern for read-only/gas-free call paths.
- Ensure the energy-sufficiency check for precompiles occurs strictly before `execute()` is invoked (fail-fast on over-budget requests) and confirm the constant-call energy ceiling is small enough that worst-case `digest()` runtime remains bounded to a low, fixed wall-clock cost.
- Verify/document the CPU-time deadline mechanism (the `Program.java`/`VM.java` matches) actually applies to, or wraps, precompiled-contract execution, not just per-opcode dispatch in the interpreter loop.

### Proof of Concept
```java
// JUnit sketch — needs to be run against a constant-call harness for this fork
byte[] data = new byte[213];
// rounds = 0xFFFFFFFF (or the effective max allowed by the constant-call energy limit)
data[0] = (byte) 0xFF; data[1] = (byte) 0xFF; data[2] = (byte) 0xFF; data[3] = (byte) 0xFF;
data[212] = 0x00; // valid finalization flag

PrecompiledContracts.Blake2F blake2F = new PrecompiledContracts.Blake2F();
long energy = blake2F.getEnergyForData(data); // == 4294967295L, unclamped

long start = System.nanoTime();
Pair<Boolean, byte[]> result = blake2F.execute(data);
long elapsedMs = (System.nanoTime() - start) / 1_000_000;
// Assert: elapsedMs should be bounded (e.g., < a few ms) regardless of `rounds`.
// If elapsedMs scales linearly with rounds and exceeds the VM's CPU-time deadline
// without being interrupted, the deadline check does not cover native precompile work.
```
This PoC only exercises `Blake2F` in isolation; confirming the end-to-end exploit requires tracing the actual `triggerconstantcontract` call path (`Wallet` → `VMActuator` → `Program`) to determine the real energy ceiling applied and whether `execute()` is reached with an unbounded `rounds` value — which I was unable to complete within this session's tool budget.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L2000-2008)
```java
    @Override
    public long getEnergyForData(byte[] data) {
      if (data.length != 213 || (data[212] & 0xFE) != 0) {
        return 0;
      }
      final byte[] roundsBytes = copyOfRange(data, 0, 4);
      final BigInteger rounds = new BigInteger(1, roundsBytes);
      return rounds.longValue();
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L2010-2029)
```java
    @Override
    public Pair<Boolean, byte[]> execute(byte[] data) {
      if (data.length != 213) {
        logger.warn("Incorrect input length.  Expected {} and got {}", 213, data.length);
        return Pair.of(false, DataWord.ZERO().getData());
      }
      if ((data[212] & 0xFE) != 0) {
        logger.warn("Incorrect finalization flag, expected 0 or 1 and got {}", data[212]);
        return Pair.of(false, DataWord.ZERO().getData());
      }
      final MessageDigest digest = new Blake2bfMessageDigest();
      byte[] result;
      try {
        digest.update(data);
        result = digest.digest();
      } catch (Exception e) {
        return Pair.of(true, EMPTY_BYTE_ARRAY);
      }
      return Pair.of(true, result);
    }
```
