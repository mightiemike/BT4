### Title
Unbounded array-length fields in `BatchValidateSign`/`ValidateMultiSign` precompiles allow cheap memory-exhaustion DoS against validators - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The `BatchValidateSign` (address `0x...9`) and `ValidateMultiSign` (address `0x...a`) precompiled contracts parse an attacker-controlled 32-byte "array length" word out of the call data and immediately use it to allocate a Java object array (`new byte[len][]`), without bounding `len` against the actual size of the supplied call data or charging energy proportional to it. This is the same bug class as SEDA H-16: an unbounded length parameter drives a huge memory allocation whose gas/energy cost is computed from something else (the real, small call-data size), letting an attacker crash validator JVMs with an `OutOfMemoryError` for negligible cost.

### Finding Description
`extractBytesArray`, `extractSigArray`, and `extractBytes32Array` all read a length field directly from parsed `DataWord`s and allocate before validating it against the real payload size: [1](#0-0) 

`ValidateMultiSign.execute` calls `extractSigArray`/`extractBytesArray` and only bounds the array size (`MAX_SIZE`) when the `allowTvmSelfdestructRestriction` chain parameter is active; the legacy `extractBytesArray` branch used when that flag is off has no bound at all: [2](#0-1) 

`BatchValidateSign.doExecute` has the identical pattern, and critically, `extractBytes32Array` for `addresses` is invoked unconditionally after the (feature-gated) size guard, and the `getEnergyForData` calculation is based only on the real `data.length`, not on the attacker-supplied length word used for allocation: [3](#0-2) 

`ALLOW_TVM_SELFDESTRUCT_RESTRICTION` defaults to disabled (`0`) unless a governance proposal has explicitly activated it, i.e. the code that actually bounds array sizes to `MAX_SIZE` (5 for `ValidateMultiSign`, 16 for `BatchValidateSign`) is opt-in, not the baseline behavior: [4](#0-3) 

Both precompiles are reachable by any unprivileged account through a normal `CALL`/`TriggerSmartContract` to the fixed precompile addresses, gated only by the long-active `allowTvmSolidity059` flag: [5](#0-4) 

An attacker can craft call data that is small (few hundred bytes, so `getEnergyForData` computes a tiny/zero `cnt` and hence negligible energy) but whose embedded "array length" word is `Integer.MAX_VALUE`, causing `new byte[len][]` to attempt to allocate roughly `len * 8` bytes (~17 GB of null references on a 64-bit JVM) purely for the array of references, immediately before any element is even read.

### Impact Explanation
Triggering this path throws (or nearly throws) `OutOfMemoryError`/`NegativeArraySizeException` inside the TVM executor thread of every validator that processes the transaction during block application, which is deterministic and reproducible by any node re-executing the same block — this can crash or destabilize multiple validators simultaneously and risk a chain halt, matching the impact class of the SEDA H-16 report (validator OOM/DoS from unbounded VM-import length parameters).

### Likelihood Explanation
Likelihood is high in the default configuration: no privileged role, no dependency on other bugs, and no leaked keys are needed — any account can broadcast a `TriggerSmartContract` transaction that performs a `CALL` to `0x...9`/`0x...a` with attacker-chosen calldata. The mitigating `MAX_SIZE` bound only applies once `ALLOW_TVM_SELFDESTRUCT_RESTRICTION` is activated via committee proposal; prior to (or absent) that activation, or via the still-present unconditional `extractBytes32Array` call ordering, the length is effectively unbounded relative to the tiny energy actually charged.

### Recommendation
- Bound the parsed array-length words (`sigArraySize`, `addrArraySize`, and any length read in `extractBytesArray`/`extractSigArray`/`extractBytes32Array`) to `MAX_SIZE` unconditionally, not only when `allowTvmSelfdestructRestriction` is enabled.
- Validate that a claimed length is consistent with the actual remaining `data`/`rawData` length before allocating any array (`len * itemSize <= data.length`), similar to the existing `isValidAbiEncoding` check used for the Osaka TIP-854 guard.
- Make `getEnergyForData` charge energy proportional to the claimed array length itself (not just the physical calldata size) so a mismatch cannot be free.

### Proof of Concept
1. Deploy any contract, or use a raw `TriggerSmartContract`, that performs a low-level `CALL` to precompile address `0x...0000009` (BatchValidateSign) or `0x...000000a` (ValidateMultiSign).
2. Craft the ABI-encoded calldata so that:
   - `words[1]` (or `words[3]` for ValidateMultiSign) points to an offset within the small, real calldata.
   - The word at that offset (`sigArraySize`/`len`) is set to `0x7FFFFFFF`.
   - Total calldata remains only a few hundred bytes, so `getEnergyForData` returns near-zero energy.
3. When the transaction executes, `extractSigArray`/`extractBytesArray`/`extractBytes32Array` executes `new byte[0x7FFFFFFF][]`, attempting a multi-gigabyte allocation on every validator node that processes the block, for negligible gas cost.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L254-259)
```java
    if (VMConfig.allowTvmSolidity059() && address.equals(batchValidateSignAddr)) {
      return batchValidateSign;
    }
    if (VMConfig.allowTvmSolidity059() && address.equals(validateMultiSignAddr)) {
      return validateMultiSign;
    }
```

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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1052-1078)
```java
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

      if (signatures.length == 0 || signatures.length > MAX_SIZE) {
        return Pair.of(true, DATA_FALSE);
      }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1138-1181)
```java
    public long getEnergyForData(byte[] data) {
      long cnt = (data.length / WORD_SIZE - 5) / 6;
      // one sign 1500, half of ecrecover
      return cnt * ENGERYPERSIGN;
    }

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

    private Pair<Boolean, byte[]> doExecute(byte[] data)
        throws InterruptedException, ExecutionException {
      if (VMConfig.allowTvmOsaka()
          && !isValidAbiEncoding(data, ABI_HEADER_WORDS, ABI_ITEM_WORDS)) {
        return Pair.of(false, EMPTY_BYTE_ARRAY);
      }
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
      int cnt = signatures.length;
      if (cnt == 0 || cnt > MAX_SIZE || signatures.length != addresses.length) {
        return Pair.of(true, DATA_FALSE);
      }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L2976-2989)
```java
  public long getAllowTvmSelfdestructRestriction() {
    return Optional.ofNullable(getUnchecked(ALLOW_TVM_SELFDESTRUCT_RESTRICTION))
        .map(BytesCapsule::getData)
        .map(ByteArray::toLong)
        .orElse(0L);
  }

  public void saveAllowTvmSelfdestructRestriction(long value) {
    this.put(ALLOW_TVM_SELFDESTRUCT_RESTRICTION, new BytesCapsule(ByteArray.fromLong(value)));
  }

  public boolean allowTvmSelfdestructRestriction() {
    return getAllowTvmSelfdestructRestriction() == 1L;
  }
```
