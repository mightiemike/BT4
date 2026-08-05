## Analog Found: Unchecked Integer Arithmetic on Attacker-Controlled Offsets in `BatchValidateSign` Precompile

### Title
Unchecked integer overflow in offset arithmetic during `BatchValidateSign` calldata array decoding - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The Pico bug class is an unchecked `offset + i + j` addition used to compute an array index for reading attacker-supplied file data, which can wrap around and cause reads from an unintended location. `java-tron`'s `BatchValidateSign` precompiled contract, reachable by any unprivileged smart-contract caller via TVM, performs structurally identical unchecked `int` arithmetic on attacker-controlled offsets/lengths derived from call data before indexing into the `words`/`data` arrays used to extract signatures and addresses for signature verification.

### Finding Description
`BatchValidateSign.doExecute()` decodes the raw call data into `DataWord[] words` and then derives array offsets purely from attacker-controlled values without any overflow-safe arithmetic: [1](#0-0) 

This leads into the helper decoders `extractBytesArray`/`extractSigArray`/`extractBytes32Array`: [2](#0-1) [3](#0-2) [4](#0-3) 

In `extractBytesArray`, `offset`, `i`, and `bytesOffset` are all `int` values sourced from `DataWord.intValueSafe()` calls on attacker-supplied call data words, and are combined with plain (unchecked) `int` addition/multiplication:
```java
int bytesOffset = words[offset + i + 1].intValueSafe() / WORD_SIZE;
int bytesLen = words[offset + bytesOffset + 1].intValueSafe();
bytesArray[i] = extractBytes(data, (bytesOffset + offset + 2) * WORD_SIZE, bytesLen);
```
This is the same bug class as the Pico ELF report: an unchecked sum of attacker-influenced offset/index terms is used directly to compute a byte-array access position. Just as `(offset + i + j) as usize` in Rust can silently wrap to a small value passed to `source_code.get(offset)`, `(bytesOffset + offset + 2) * WORD_SIZE` in Java can integer-overflow (`int` arithmetic wraps in two's complement) and either throw an uncaught `ArrayIndexOutOfBoundsException` inside `extractBytes`'s `Arrays.copyOfRange` call, or — depending on the exact wrap value — silently produce a small, in-bounds but semantically wrong offset that reads unrelated bytes from `data` as if they were a signature.

Only the `sigArraySize`/`addrArraySize` bound is checked, and only when `VMConfig.allowTvmSelfdestructRestriction()` is enabled: [5](#0-4) 
There is no check that `offset + i + 1`, `offset + bytesOffset + 1`, or `(bytesOffset + offset + 2) * WORD_SIZE` stay within `Integer` range before being used as array indices or byte offsets, mirroring the missing `checked_add`/`checked_mul` guard identified as the root cause in the Pico report.

### Impact Explanation
`BatchValidateSign` is a public precompiled contract invocable by any unprivileged caller through a TVM contract, so the attack surface matches the Pico report's "attacker submits crafted input for parsing" pattern exactly. Two outcomes are possible depending on how the `int` overflow wraps:
1. An uncaught `ArrayIndexOutOfBoundsException`/`IllegalArgumentException` is thrown from `Arrays.copyOfRange` inside `extractBytes`, causing abnormal termination of that specific transaction's execution.
2. The overflow wraps to a small, valid, but wrong offset, causing `extractBytes` to silently read the wrong slice of `data` as "signature" bytes, which are then fed into `recoverAddrBySign` — producing an incorrect/unintended recovered address comparison result for the multi-signature validation this precompile is meant to perform.

Outcome (2) directly undermines the correctness of a signature/accounting-verification primitive exposed on-chain, which fits the "invalid-state/divergence" category: the precompile's result (`res[i] = 1` or `0`) is used by calling contracts to gate authorization logic, so a wrong offset producing spurious recovered addresses can corrupt that authorization decision for arbitrary caller-supplied data.

### Likelihood Explanation
Reaching this code path requires only invoking the `BatchValidateSign` precompile (address is public and callable by any contract/EOA-initiated contract call) with crafted call data words designed so that `offset`, `i`, and `bytesOffset` sum/multiply past `Integer.MAX_VALUE`/`Integer.MIN_VALUE`. Because `intValueSafe()` allows attacker-chosen large `int` values in the words array, and no explicit range check exists before the arithmetic, this is straightforward to trigger deliberately, similar to the ease of constructing the malicious ELF segment offsets described in the source report.

### Recommendation
Use overflow-checked arithmetic (e.g. `Math.addExact`/`Math.multiplyExact`, mirroring `addSafely` already used elsewhere in this file for `ModExp`) for all offset/index computations in `extractBytesArray`, `extractSigArray`, and `extractBytes32Array` before they are used to index `words` or slice `data`, and reject the call (return `Pair.of(false, ...)`) rather than let the overflow propagate into unchecked array access.

### Proof of Concept
No concrete PoC was constructed since verifying exact wraparound values (and whether the resulting exception is caught upstream and converted into a normal TVM revert versus propagating further) requires runtime testing beyond static code review. The analysis is based on the unchecked `int` arithmetic pattern shown in the cited lines, which structurally mirrors the reported Rust overflow.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L390-397)
```java
  private static byte[][] extractBytes32Array(DataWord[] words, int offset) {
    int len = words[offset].intValueSafe();
    byte[][] bytes32Array = new byte[len][];
    for (int i = 0; i < len; i++) {
      bytes32Array[i] = words[offset + i + 1].getData();
    }
    return bytes32Array;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L399-412)
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
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L414-430)
```java
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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1162-1177)
```java
      DataWord[] words = DataWord.parseArray(data);
      byte[] hash = words[0].getData();

      if (VMConfig.allowTvmSelfdestructRestriction()) {
        int sigArraySize = words[words[1].intValueSafe() / WORD_SIZE].intValueSafe();
        int addrArraySize = words[words[2].intValueSafe() / WORD_SIZE].intValueSafe();
        if (sigArraySize > MAX_SIZE || addrArraySize > MAX_SIZE) {
          return Pair.of(true, DATA_FALSE);
        }
      }

      byte[][] signatures = VMConfig.allowTvmSelfdestructRestriction() ?
          extractSigArray(words, words[1].intValueSafe() / WORD_SIZE, data) :
          extractBytesArray(words, words[1].intValueSafe() / WORD_SIZE, data);
      byte[][] addresses = extractBytes32Array(
          words, words[2].intValueSafe() / WORD_SIZE);
```
