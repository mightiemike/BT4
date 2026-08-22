### Title
Unchecked integer arithmetic in ABI offset decoding causes overflow/OOB read in TVM precompiled contract byte-array extraction - (File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java)

### Summary
`PrecompiledContracts.extractBytesArray()` and `PrecompiledContracts.extractSigArray()` decode attacker-supplied ABI call data (the same untrusted, user-controlled input class as the Pico ELF `p_offset`/`i`/`j` fields) by summing multiple int-typed, fully attacker-controlled values and then indexing a byte array with the result, exactly mirroring the unchecked `offset + i + j` pattern from the referenced report.

### Finding Description
`extractBytesArray` and `extractSigArray` compute an offset into the raw calldata `byte[] data` by chaining several `intValueSafe()`-derived values from attacker-controlled `DataWord[] words` (decoded straight from calldata) without any overflow checking: [1](#0-0) [2](#0-1) 

Both feed into: [3](#0-2) 

`bytesOffset` comes from `words[offset + i + 1].intValueSafe()` — an int derived from a 256-bit attacker-controlled word — and is then combined via `(bytesOffset + offset + 2) * WORD_SIZE`. Because `bytesOffset` can be crafted close to `Integer.MAX_VALUE / WORD_SIZE`, the addition `bytesOffset + offset + 2` and/or the subsequent `* WORD_SIZE` multiplication can silently overflow the 32-bit `int`, producing a wrapped-around (possibly negative) offset — the same root cause described in the Pico report where `offset + i + j` overflows before being used to index into `source_code`. This wrapped value is passed directly into `Arrays.copyOfRange(data, offset, offset + len)` with no bounds validation prior to the call.

This is reachable from any account executing a contract call that dispatches to the batch-signature-validation precompiled contract (evidenced by `BatchValidateSignContractTest`, which exercises these code paths), i.e., from ordinary, unprivileged TVM execution triggered by a broadcast transaction — matching the required "contract call" root reachability.

### Impact Explanation
An attacker can craft a transaction invoking the precompiled contract with adversarial word offsets so that the computed offset overflows to a negative or otherwise out-of-range value. `Arrays.copyOfRange` with a negative `from` index throws `ArrayIndexOutOfBoundsException`/`IllegalArgumentException`, and with an out-of-range `to` index can throw further unchecked exceptions. Depending on how these exceptions propagate through the VM execution/precompiled-contract dispatch layer, this can cause abnormal termination of the call (DoS for that transaction/node execution path) or non-deterministic behavior if different nodes' JVMs/JIT handle the wrap differently — but since Java `int` overflow is well-defined (twos-complement wraparound) and deterministic across all conforming JVMs, the primary practical impact is an uncaught-exception based execution failure/DoS for the specific transaction rather than consensus divergence.

### Likelihood Explanation
Reaching this path only requires submitting a normal contract call to the affected precompiled contract with crafted calldata values — no privileged role, leaked key, or malicious peer is needed, satisfying the "unprivileged, reachable from a broadcast transaction" requirement. However, likelihood of causing more than a single failed/exceptional call is limited because `intValueSafe()` bounds an individual word to `int` range, and the actual severity depends on how the surrounding precompiled-contract executor traps arithmetic/array exceptions during execution (not fully confirmed in the available index).

### Recommendation
Use checked/saturating arithmetic (e.g., `Math.addExact`/`Math.multiplyExact` or explicit range validation) when computing `bytesOffset`, `offset + bytesOffset + 2`, and the final byte offset/length passed to `extractBytes`, rejecting the precompiled-contract call (returning a decode failure) instead of allowing silent 32-bit wraparound, analogous to the `checked_add` mitigation adopted upstream for the Pico `Elf::new()` fix.

### Proof of Concept
Not executed against the live codebase; based on static analysis of `extractBytesArray`/`extractSigArray`/`extractBytes` in `PrecompiledContracts.java`. A concrete PoC would require crafting an ABI-encoded call to the batch-signature-validation precompiled contract with `words[offset+i+1]` values near `Integer.MAX_VALUE / WORD_SIZE` to trigger the `(bytesOffset + offset + 2) * WORD_SIZE` overflow in `extractBytesArray`/`extractSigArray`, verified via `BatchValidateSignContractTest` as the exercising test harness. [4](#0-3)

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L399-430)
```java
  private static byte[][] extractBytesArray(DataWord[] words, int offset, byte[] data) {
    if (offset > words.length - 1) {
      return new byte[0][];
    }
    int len = words[offset].intValueSafe();
    byte[][] bytesArray = new byte[len][];
    for (int i = 0; i < len; i++) {
      int bytesOffset = words[offset + i + 1].intValueSafe() / WORD_SIZE;
      int bytesLen = words[offset + bytesOffset + 1].intValueSafe();
      bytesArray[i] = extractBytes(data, (bytesOffset + offset + 2) * WORD_SIZE,
          bytesLen);
    }
    return bytesArray;
  }

  private static byte[][] extractSigArray(DataWord[] words, int offset, byte[] data) {
    if (offset > words.length - 1) {
      return new byte[0][];
    }
    int len = words[offset].intValueSafe();
    byte[][] bytesArray = new byte[len][];
    for (int i = 0; i < len; i++) {
      int bytesOffset = words[offset + i + 1].intValueSafe() / WORD_SIZE;
      bytesArray[i] = extractBytes(data, (bytesOffset + offset + 2) * WORD_SIZE,
          SIG_LENGTH);
    }
    return bytesArray;
  }

  private static byte[] extractBytes(byte[] data, int offset, int len) {
    return Arrays.copyOfRange(data, offset, offset + len);
  }
```
