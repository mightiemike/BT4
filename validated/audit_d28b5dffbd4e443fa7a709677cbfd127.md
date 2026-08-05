## Title
Unchecked length field used to slice precompiled-contract calldata can trigger unbounded allocation / uncaught exception - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

## Summary
The Celo report describes `MetaTransactionWallet.executeTransactions` slicing a packed `data` blob using attacker-supplied length values without ever checking that those lengths are consistent with (or bounded by) the size of the actual data supplied. The analogous pattern exists in java-tron's `ValidateMultiSign` / `BatchValidateSign` precompiled contracts, where the helper `extractBytesArray` reads a length field directly out of the attacker-controlled calldata and uses it to slice the same calldata buffer with no bound check against the buffer's real size.

## Finding Description
`extractBytesArray` decodes a dynamic `bytes[]` ABI argument manually: [1](#0-0) 

`bytesLen` at line 407 is taken straight from a 32-byte word inside the raw calldata (`words[offset + bytesOffset + 1].intValueSafe()`), fully controlled by the caller. It is then passed to `extractBytes`, which performs `Arrays.copyOfRange(data, offset, offset + len)`: [2](#0-1) 

There is no validation that `offset + bytesLen` stays within `data.length` (the actual size of the calldata that was paid for/metered), i.e. the code never enforces that the declared item length is consistent with the real amount of data supplied — exactly the missing check identified in the Celo report (`data` length should equal sum of declared segment lengths before slicing).

This helper is used by both `ValidateMultiSign.execute` and `BatchValidateSign.execute` whenever `VMConfig.allowTvmSelfdestructRestriction()` is not enabled (legacy decode path): [3](#0-2) 

In `ValidateMultiSign.execute`, the call to `extractBytesArray` happens **before** the surrounding `try { ... } catch (Throwable t)` block that only wraps the account/permission lookup logic, so any exception raised while slicing (e.g. `NegativeArraySizeException` from integer overflow of `offset + len`, or an `OutOfMemoryError` from an attacker-declared multi-gigabyte length) is not caught inside this method and propagates to the caller.

## Impact Explanation
Energy for `ValidateMultiSign`/`BatchValidateSign` is charged based on the *physical* calldata length (`data.length / WORD_SIZE`), not on the attacker-declared inner `bytesLen` field: [4](#0-3) 

Because `bytesLen` is decoupled from the metered input size, a small, cheap call can embed an arbitrarily large declared length, causing the JVM to attempt allocating an oversized array in `Arrays.copyOfRange`. This is underpriced public work: minimal energy purchases disproportionate memory/CPU cost, and an uncaught exception during precompile execution risks inconsistent/undefined handling of the enclosing transaction/block processing (an invalid-state or halt-class outcome) rather than a clean, priced failure.

## Likelihood Explanation
Both precompiles are reachable by any unprivileged account or contract making a normal `STATICCALL`/`CALL` to their fixed precompile addresses with attacker-chosen calldata; the vulnerable `extractBytesArray` code path is the default/legacy one used whenever `allowTvmSelfdestructRestriction()` is not active (older or non-upgraded chain configurations). No special privileges are required to trigger it.

## Recommendation
In `extractBytesArray` (and the equivalent decode logic in `extractSigArray`/related helpers), validate that the declared item offset/length stay within the bounds of the actual `data.length` before calling `Arrays.copyOfRange` — analogous to requiring the Celo `data` parameter's length equal the sum of declared `dataLengths`. Reject the call (return `Pair.of(false, EMPTY_BYTE_ARRAY)`) instead of allowing an unchecked slice, and ensure the check happens for every decode path, not only the newer `isValidAbiEncoding`/`allowTvmSelfdestructRestriction` gated path.

## Proof of Concept
1. Craft calldata for `ValidateMultiSign` (`validatemultisign(address,uint256,bytes32,bytes[])`) with a valid outer ABI header, but set the length word of one `bytes[]` element (the word read at `words[offset + bytesOffset + 1]`) to a very large value (e.g., `0x7FFFFFFF`).
2. Submit a transaction/contract call invoking this precompile with `allowTvmSelfdestructRestriction()` disabled (legacy path), so `extractBytesArray` is used instead of the fixed-length `extractSigArray`.
3. `extractBytes` calls `Arrays.copyOfRange(data, offset, offset + 0x7FFFFFFF)`, attempting to allocate a ~2GB array (or triggering integer-overflow-driven `NegativeArraySizeException`) for a call whose actual paid-for calldata is only a few hundred bytes — demonstrating the underpriced-work / uncaught-exception condition described above.

Note: exact behavior depends on `DataWord.intValueSafe()`'s overflow-capping semantics, which could not be fully confirmed with the available tooling; a Devin session with full repo access would be needed to trace `intValueSafe()` and the top-level precompile-invocation exception handling in `Program.java` to fully confirm whether the uncaught exception escapes to a block-processing failure versus being caught generically elsewhere.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L428-430)
```java
  private static byte[] extractBytes(byte[] data, int offset, int len) {
    return Arrays.copyOfRange(data, offset, offset + len);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1045-1049)
```java
    public long getEnergyForData(byte[] data) {
      long cnt = (data.length / WORD_SIZE - 5) / 5;
      // one sign 1500, half of ecrecover
      return cnt * ENGERYPERSIGN;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1072-1078)
```java
      byte[][] signatures = VMConfig.allowTvmSelfdestructRestriction() ?
          extractSigArray(words, words[3].intValueSafe() / WORD_SIZE, rawData) :
          extractBytesArray(words, words[3].intValueSafe() / WORD_SIZE, rawData);

      if (signatures.length == 0 || signatures.length > MAX_SIZE) {
        return Pair.of(true, DATA_FALSE);
      }
```
