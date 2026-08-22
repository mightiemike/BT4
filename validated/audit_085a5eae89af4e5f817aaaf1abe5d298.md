Based on the evidence gathered, I found a strong analog in java-tron's precompiled contract layer.

### Title
`abi.decode`-style malformed calldata in TVM precompiled contracts (`ValidateMultiSign`/`BatchValidateSign`) can raise unhandled exceptions before activation of the TIP-854 guard - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
The `ValidateMultiSign` and `BatchValidateSign` precompiled contracts decode raw calldata into `DataWord[]` arrays and then compute byte offsets/lengths (`extractBytesArray`, `extractSigArray`, `extractBytes32Array`) assuming the ABI-encoded shape is well-formed, mirroring the `abi.decode`-on-untrusted-data pattern described in the external report.

### Finding Description
Helper decoders such as `extractSigArray`/`extractBytesArray`/`extractBytes32Array` compute offsets from attacker-controlled header words and then call `Arrays.copyOfRange` via `extractBytes` without bounds checking against the actual data length [1](#0-0) . A dedicated `isValidAbiEncoding` guard was added later to reject malformed input for the header/item-word shape used by `validateMultiSign` and `batchValidateSign` [2](#0-1) , but this guard is explicitly gated behind `VMConfig.allowTvmOsaka()`/TIP-854 activation. The corresponding test explicitly documents that before activation, malformed calldata reaches the legacy decoder and this precompile "has no outer catch, so a too-short input raises inside the decoder; that is the documented pre-activation failure mode the TIP explicitly preserves" [3](#0-2) . This is the same root cause class as the report: decoding logic assumes correctly shaped input, and when it is not, execution throws instead of failing gracefully, and the calling code (a smart contract that assumed the precompiled call would just return `(false, "")` rather than reverting) breaks the same way `_swap`/`_trySwap` breaks in the external report when `abi.decode` reverts unexpectedly.

### Impact Explanation
If a smart contract calls `validateMultiSign`/`batchValidateSign` with malformed calldata (e.g., forwarding attacker-controlled signature arrays) and assumes the call cannot revert—analogous to the `_trySwap` assumption in the report—the entire enclosing transaction/message call fails unexpectedly. Depending on how the calling contract structured its logic (e.g., committing token transfers before validating signatures, or relying on this precompile inside a try/catch expecting only a boolean failure), funds or state changes already performed earlier in the same call could be rolled back inconsistently, or the call could revert entirely, causing loss of gas/energy and stranded intermediate state for the caller, consistent with the report's "tokens might be lost" scenario for `_trySwap`.

### Likelihood Explanation
This only manifests pre-TIP-854 activation (when `VMConfig.allowTvmOsaka()` is false) or for any precompiled path not covered by `isValidAbiEncoding`, and requires a contract author to call these precompiles with attacker-influenced signature data — a realistic and common pattern (multi-sig wallets, batch verification of user-supplied signature bundles) since `validateMultiSign`/`batchValidateSign` exist specifically to validate externally supplied signatures.

### Recommendation
Extend the `isValidAbiEncoding` bounds-check guard (currently tied to the TIP-854/`allowTvmOsaka` flag) to be unconditional for all activation states, and audit all other precompiled-contract decoders (`extractBytesArray`, `extractSigArray`, `extractBytes32Array`, and any other raw-offset decoding in `PrecompiledContracts.java`) for the same missing bounds validation, ensuring malformed calldata always yields a graceful `(false, empty)` result rather than an uncaught `ArrayIndexOutOfBoundsException`/`RuntimeException` propagating out of the precompile.

### Proof of Concept
The existing regression test demonstrates the exact failure mode: calling `contract.execute(new byte[(5 + 1) * 32])` (malformed/truncated calldata) with `VMConfig.initAllowTvmOsaka(0)` (pre-activation) causes the legacy decoder to potentially throw a `RuntimeException` instead of returning `(false, empty)`, which the test explicitly tolerates as documented existing behavior [4](#0-3) .

### Citations

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

**File:** framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java (L244-260)
```java
  // TIP-854: before activation, malformed calldata reaches the legacy decoder.
  // Assert the guard is not taken — this precompile has no outer catch, so a
  // too-short input raises inside the decoder; that is the documented
  // pre-activation failure mode the TIP explicitly preserves.
  @Test
  public void testTip854PreActivationNoOp() {
    VMConfig.initAllowTvmOsaka(0);
    contract.setRepository(RepositoryImpl.createRoot(StoreFactory.getInstance()));
    try {
      Pair<Boolean, byte[]> ret = contract.execute(new byte[(5 + 1) * 32]);
      // If the decoder happened to handle it without raising, we must not have
      // taken the post-activation reject path (false, empty).
      Assert.assertNotSame(ByteUtil.EMPTY_BYTE_ARRAY, ret.getRight());
    } catch (RuntimeException expectedLegacyBehaviour) {
      // Pre-activation: decoder may throw — this is the existing behaviour.
    }
  }
```
