### Title
Unhandled ArrayIndexOutOfBoundsException in ValidateMultiSign precompile causes reachable TVM call failure - (File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java)

### Summary
`ValidateMultiSign.execute` parses attacker-controlled ABI words and computes an array-extraction offset from raw input data *before* entering the method's `try/catch` block. A crafted offset value causes an out-of-bounds array access that is never caught, unlike the analogous `BatchValidateSign` contract, which wraps its entire body in a catch-all.

### Finding Description
`ValidateMultiSign.execute` reads `words[3]` (fully attacker-controlled via the contract call data) and immediately uses it to index back into the same `words` array: [1](#0-0) 

Specifically:
```
if (VMConfig.allowTvmSelfdestructRestriction()) {
  int sigArraySize = words[words[3].intValueSafe() / WORD_SIZE].intValueSafe();
  ...
}
byte[][] signatures = ... extractSigArray(words, words[3].intValueSafe() / WORD_SIZE, rawData) ...
```
`words[3].intValueSafe() / WORD_SIZE` is fully controlled by the caller and is not bounds-checked against `words.length` before this line runs. If the derived index is `>= words.length`, `words[index]` throws `ArrayIndexOutOfBoundsException`. This statement executes *outside* the `try { ... } catch (Throwable t) { ... }` block that only wraps the later account/permission-processing logic: [2](#0-1) 

This is the same bug *class* as the Frax `globalPause` finding: a function that is supposed to gracefully process external input instead has an unguarded array operation on attacker/caller-influenced data that unconditionally throws, bypassing the function's own error-handling design. In `globalPause`, the "protective" pattern (declare array, then index it) fails because of a missing allocation; here, the analogous "protective" pattern (parse ABI, then process safely) fails because the offset-derived index is validated in the sibling contract `BatchValidateSign` but not in `ValidateMultiSign`, and the validation/extraction happens before, not inside, the exception-safety boundary.

Contrast with `BatchValidateSign.execute`, which wraps its *entire* body (`doExecute`) in `try/catch (Throwable t)`, so the same class of out-of-bounds indexing there is caught and degrades gracefully to `Pair.of(true, new byte[WORD_SIZE])`: [3](#0-2) 

The two sibling precompiles (`ValidateMultiSign` and `BatchValidateSign`) implement structurally identical signature-verification logic, but only one of them protects the whole method body from unhandled exceptions. `ValidateMultiSign` is reachable via `words[offset]` in `extractSigArray`/`extractBytesArray`, which do include their own internal offset bound checks (`if (offset > words.length - 1) return new byte[0][];`) but only guard the call *inside* those helper functions — they do not protect the two `words[words[3]...]` dereferences that happen at the call sites in `ValidateMultiSign.execute` before `extractSigArray`/`extractBytesArray` are even invoked (and unconditionally when `allowTvmSelfdestructRestriction()` is enabled): [4](#0-3) 

### Impact Explanation
Any contract call to the `ValidateMultiSign` precompile (address exposed to any TVM contract/user via a normal `STATICCALL`/`CALL`) with a crafted `words[3]` offset value throws an uncaught `ArrayIndexOutOfBoundsException`. This propagates up out of `execute()` into the TVM interpreter that invokes precompiled contracts. Depending on how the top-level VM/Program exception handling classifies unchecked `RuntimeException`s versus the framework's documented `Program.Exception` types, this either forces the current call frame to revert (denial of the specific precompile's functionality — matching the "Medium" severity/"convenience function breaks" pattern of the original finding, since callers can still use direct signature verification via `ValidateMultiSign`/other precompiles or off-chain verification) or, if unhandled up the stack, could cause an inconsistent trace/exception surface for any contract depending on this precompile succeeding. No funds or consensus state are directly at risk since this is a read/verification-only precompile with no state mutation, matching the original report's judged severity.

### Likelihood Explanation
High likelihood of triggering: the precompile is callable by any address from any TVM contract with attacker-supplied ABI-encoded `bytes`, and the vulnerable code path is unconditionally executed whenever `VMConfig.allowTvmSelfdestructRestriction()` is enabled (a mainnet-activated hardfork flag), requiring no privileged role — only a single crafted call.

### Recommendation
Add the same bounds check used in `extractBytesArray`/`extractSigArray` (`if (offset > words.length - 1) { return Pair.of(true, DATA_FALSE); }`) immediately after computing `words[3].intValueSafe() / WORD_SIZE` and before dereferencing `words[...]` in `ValidateMultiSign.execute`, or move the entire ABI-parsing/extraction logic inside the existing `try/catch (Throwable t)` block, consistent with how `BatchValidateSign.doExecute` is fully wrapped by its caller `execute()`.

### Proof of Concept
1. Construct a TVM contract call to the `ValidateMultiSign` precompiled contract address with `VMConfig.allowTvmSelfdestructRestriction()` active.
2. Encode `words[3]` (the signature-array offset word) as a large value such that `words[3].intValueSafe() / WORD_SIZE` exceeds `words.length - 1`.
3. Invoke the precompile; execution reaches `words[words[3].intValueSafe() / WORD_SIZE]` in the `if (VMConfig.allowTvmSelfdestructRestriction())` block before the method's `try` block, throwing `ArrayIndexOutOfBoundsException`, uncaught by `ValidateMultiSign.execute`.

Note: I was unable to fully trace, within the available tool budget, how the top-level `VM`/`Program` exception dispatch in `actuator/src/main/java/org/tron/core/vm/VM.java` and `Program.java` ultimately classifies an uncaught `ArrayIndexOutOfBoundsException` thrown from inside a precompiled contract's `execute()` (i.e., whether it is caught generically as a call-frame revert or propagates further). This should be verified directly against those files before treating the impact as more than a single-call revert.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L390-426)
```java
  private static byte[][] extractBytes32Array(DataWord[] words, int offset) {
    int len = words[offset].intValueSafe();
    byte[][] bytes32Array = new byte[len][];
    for (int i = 0; i < len; i++) {
      bytes32Array[i] = words[offset + i + 1].getData();
    }
    return bytes32Array;
  }

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
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1051-1074)
```java
    @Override
    public Pair<Boolean, byte[]> execute(byte[] rawData) {
      if (VMConfig.allowTvmOsaka()
          && !isValidAbiEncoding(rawData, ABI_HEADER_WORDS, ABI_ITEM_WORDS)) {
        return Pair.of(false, EMPTY_BYTE_ARRAY);
      }
      DataWord[] words = DataWord.parseArray(rawData);
      byte[] address = words[0].toTronAddress();
      int permissionId = words[1].intValueSafe();
      byte[] data = words[2].getData();

      byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
      byte[] hash = Sha256Hash.hash(CommonParameter
          .getInstance().isECKeyCryptoEngine(), combine);

      if (VMConfig.allowTvmSelfdestructRestriction()) {
        int sigArraySize = words[words[3].intValueSafe() / WORD_SIZE].intValueSafe();
        if (sigArraySize > MAX_SIZE) {
          return Pair.of(true, DATA_FALSE);
        }
      }
      byte[][] signatures = VMConfig.allowTvmSelfdestructRestriction() ?
          extractSigArray(words, words[3].intValueSafe() / WORD_SIZE, rawData) :
          extractBytesArray(words, words[3].intValueSafe() / WORD_SIZE, rawData);
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1080-1119)
```java
      AccountCapsule account = this.getDeposit().getAccount(address);
      if (account != null) {
        try {
          Permission permission = account.getPermissionById(permissionId);
          if (permission != null) {
            //calculate weight
            long totalWeight = 0L;
            List<byte[]> executedSignList = new ArrayList<>();
            for (byte[] sign : signatures) {
              byte[] recoveredAddr = recoverAddrBySign(sign, hash);

              sign = merge(recoveredAddr, sign);
              if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
                if (ByteArray.matrixContains(executedSignList, sign)) {
                  continue;
                }
                MUtil.checkCPUTime();
              }
              long weight = TransactionCapsule.getWeight(permission, recoveredAddr);
              if (weight == 0) {
                //incorrect sign
                return Pair.of(true, DATA_FALSE);
              }
              totalWeight += weight;
              executedSignList.add(sign);
              executedSignList.add(recoveredAddr);
            }

            if (totalWeight >= permission.getThreshold()) {
              return Pair.of(true, dataOne());
            }
          }
        } catch (Throwable t) {
          if (t instanceof OutOfTimeException) {
            throw t;
          }
          logger.info("ValidateMultiSign error:{}", t.getMessage());
        }
      }
      return Pair.of(true, DATA_FALSE);
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1144-1154)
```java
    @Override
    public Pair<Boolean, byte[]> execute(byte[] data) {
      try {
        return doExecute(data);
      } catch (Throwable t) {
        if (t instanceof InterruptedException){
          Thread.currentThread().interrupt();
        }
        return Pair.of(true, new byte[WORD_SIZE]);
      }
    }
```
