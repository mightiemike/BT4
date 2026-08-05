## Analysis Result

### Title
Unvalidated nested offset/length fields in ABI `bytes[]` decoding allow calldata field confusion in `ValidateMultiSign`/`BatchValidateSign` precompiles - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The Seaport bug relies on trusting an attacker-controlled length field to compute a decode size/offset for a `bytes` value nested inside a dynamic array, without validating that the derived pointer stays inside the intended region — causing one field's data to be substituted for another's. The same pattern exists in java-tron's `extractBytesArray`/`extractSigArray` helpers, used by the `ValidateMultiSign` and `BatchValidateSign` precompiled contracts to hand-roll ABI decoding of `bytes[]` parameters.

### Finding Description
`extractBytesArray` and `extractSigArray` derive per-element offsets and lengths directly from attacker-supplied `DataWord` values inside the calldata, with no bounds validation against the actual buffer: [1](#0-0) 

`bytesOffset` and `bytesLen` (and the analogous `bytesOffset` in `extractSigArray`) are computed purely from `words[...].intValueSafe()` with no check that `offset + bytesOffset + 1` is within `words.length`, nor that the resulting byte range lies within the intended tail of `data` reserved for that particular array element. `extractBytes` then blindly slices the raw buffer: [2](#0-1) 

The only guard introduced (under `VMConfig.allowTvmOsaka()`) is `isValidAbiEncoding`, which merely checks that the *overall* calldata length is word-aligned and consistent with a fixed header/item word count: [3](#0-2) 

This is exactly the same class of gap identified in the Seaport report: a coarse shape/length check on the outer structure does not prevent an attacker from crafting internal offset/length words that make the decoder read a *different* logical field's bytes as the field under decode (e.g., substituting one `bytes` element's payload for another's, or reading past the intended slice). In `ValidateMultiSign`, the call to `extractSigArray`/`extractBytesArray` happens **outside** the local `try { ... } catch (Throwable t)` block that wraps signature verification: [4](#0-3) 

so any `ArrayIndexOutOfBoundsException`/`NegativeArraySizeException` raised while decoding malicious offsets is not caught by that inner handler.

### Impact Explanation
Because these precompiles are the on-chain multisig signature-verification path (`validatemultisign(address,uint256,bytes32,bytes[])` / `batchvalidatesign(bytes32,bytes[],address[])`), causing the decoder to substitute one signature's bytes for another (or misread arbitrary buffer content as a "signature") is a data-confusion issue in an authentication-critical code path — the same class of impact Spearbit flagged for Seaport (signature/field substitution via crafted `bytes[]` decoding). At minimum this is an unvalidated-input robustness issue in a security-sensitive decoder that any unprivileged caller can reach with a single crafted `CALL`.

### Likelihood Explanation
Any unprivileged account or contract can invoke these precompiles directly (they are reachable via plain `CALL`), and crafting the offset/length words requires no special privilege — only knowledge of the ABI layout, comparable to constructing the Seaport PoC calldata. The `isValidAbiEncoding` gate (only active under `allowTvmOsaka`) does not validate internal offsets, so the underlying decoding weakness is present regardless of that feature flag; it only rejects grossly malformed overall shapes, not internally-inconsistent ones.

### Recommendation
Harden `extractBytesArray`/`extractSigArray` to validate, for every element: (1) `offset + bytesOffset + 1 < words.length`, (2) the derived byte range `[(bytesOffset+offset+2)*WORD_SIZE, +bytesLen)` lies fully within `data.length` and does not overlap/precede the region reserved for the array's own header/other elements, and (3) `bytesLen` is non-negative and consistent with the tail length actually available for that entry — analogous to Seaport PR #789's fix of tightening the length/size computation rather than relying on a coarse total-length mask. Wrap the `extractSigArray`/`extractBytesArray` calls in `ValidateMultiSign.execute` with the same defensive handling already used for `BatchValidateSign.doExecute` so malformed internal offsets fail closed (`Pair.of(true/false, DATA_FALSE/EMPTY_BYTE_ARRAY)`) instead of potentially throwing an uncaught exception.

### Proof of Concept
Construct calldata for `validatemultisign(address,uint256,bytes32,bytes[])` where the outer word count and header shape pass `isValidAbiEncoding`, but the per-element length word inside the `bytes[]` tail is set so that `bytesOffset` (computed from `words[offset+i+1]`) points into the region of a *different* array element's payload rather than its own length-prefixed slot — causing `extractBytes` to slice and return that other element's bytes as the current signature. This mirrors the Seaport PoC of pointing a `signature` field's offset into `extraData`'s payload region, causing the decoder to interpret unrelated calldata as the parsed field.

**Note on verification gaps:** I could not fully trace whether an uncaught `ArrayIndexOutOfBoundsException`/`NegativeArraySizeException` thrown from this code path is caught by a top-level handler in `Program.callToPrecompiledAddress` (in `actuator/src/main/java/org/tron/core/vm/program/Program.java`) and converted into a clean revert, or whether it could propagate further and cause inconsistent node behavior — the file's relevant section was not fully retrieved before the session ended. This affects whether the "exception" branch of impact (as opposed to the confirmed "field-confusion" decoding branch) constitutes a DoS/divergence risk; a Devin session with full file access should verify this before finalizing severity.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L399-426)
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
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L428-430)
```java
  private static byte[] extractBytes(byte[] data, int offset, int len) {
    return Arrays.copyOfRange(data, offset, offset + len);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L432-438)
```java
  private static boolean isValidAbiEncoding(byte[] data, int headerWords, int itemWords) {
    if (data == null || data.length % WORD_SIZE != 0) {
      return false;
    }
    long tail = subtractExact(data.length, multiplyExact(headerWords, WORD_SIZE));
    return tail > 0 && tail % multiplyExact(itemWords, WORD_SIZE) == 0;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1072-1120)
```java
      byte[][] signatures = VMConfig.allowTvmSelfdestructRestriction() ?
          extractSigArray(words, words[3].intValueSafe() / WORD_SIZE, rawData) :
          extractBytesArray(words, words[3].intValueSafe() / WORD_SIZE, rawData);

      if (signatures.length == 0 || signatures.length > MAX_SIZE) {
        return Pair.of(true, DATA_FALSE);
      }

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
    }
```
