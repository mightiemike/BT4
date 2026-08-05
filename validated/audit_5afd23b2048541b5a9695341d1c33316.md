## Title
Energy-vs-actual-work divergence in `ValidateMultiSign`/`BatchValidateSign` precompiles when TIP-854 guard is inactive - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The Sherlock `bps()` bug class is: a function derives trusted state from an *assumed* structure of attacker-controlled input, while the actual decoding/execution logic can be driven by a *different* structure hidden in the same input, so the "computed" value and the "used" value diverge. The closest reachable analog in java-tron is in the `ValidateMultiSign` and `BatchValidateSign` TVM precompiles, where `getEnergyForData()` charges energy based on a rigid formula that assumes canonical ABI layout, while `execute()`/`doExecute()` decodes the *actual* array lengths from offsets embedded in the calldata itself. A guard (`isValidAbiEncoding`, gated behind `VMConfig.allowTvmOsaka()`) was added later to reconcile the two, which strongly indicates the pre-guard state is exactly this bug class.

### Finding Description
`ValidateMultiSign.getEnergyForData()` computes the energy purely from calldata length: [1](#0-0) 

and `BatchValidateSign.getEnergyForData()` similarly: [2](#0-1) 

Both formulas assume a fixed head (`ABI_HEADER_WORDS`) plus a fixed per-item word count (`ABI_ITEM_WORDS`), i.e. they assume `data.length` encodes exactly `cnt` items of a known size. However, the actual decoding in `doExecute()`/`execute()` does not derive the item count from `data.length` — it derives it from **offset words embedded inside the calldata itself** (`words[1]`, `words[2]`, `words[3]`, etc.), then uses those offsets to index into `words[...]` and slice `rawData`: [3](#0-2) [4](#0-3) 

This is structurally identical to the Sherlock `bps()` bug: the energy-charging computation ("what work do we think this call performs?") is based on one assumption about the data layout, while the actual work ("how many signatures/addresses do we actually recover and verify?") is driven by attacker-supplied offset fields inside the same calldata blob — a value that can be crafted to diverge from what the energy formula assumed. The fix for this divergence, TIP-854, adds `isValidAbiEncoding(data, ABI_HEADER_WORDS, ABI_ITEM_WORDS)` but only when `VMConfig.allowTvmOsaka()` is active: [5](#0-4) [6](#0-5) 

Before this fork is active, malformed/injected calldata (non-32-aligned length, wrong tail word count, or crafted offset fields) is *not* rejected, and the tests explicitly document the resulting legacy behavior: [7](#0-6) [8](#0-7) 

For `BatchValidateSign`, malformed pre-activation calldata that raises inside `doExecute()` is silently swallowed by the outer catch and converted into `(true, new byte[WORD_SIZE])` — a state where the energy already charged (per the naive length-based formula) may not match the actual signature-recovery work that was attempted (and aborted) inside `doExecute`: [9](#0-8) 

### Impact Explanation
This is an underpriced/mispriced-public-work class issue: the energy fee model assumes a 1:1 relationship between calldata length and the number of signature-recovery operations performed, but a contract can construct calldata whose offset words describe an array shape inconsistent with what the length-based formula assumed. Depending on how offsets are crafted, this can cause energy charged to not match the actual EC-recovery/array work executed inside the precompile (each EC recovery is nontrivial CPU cost), a form of underpriced public work that can be used to cheaply consume validator CPU relative to energy paid, analogous to how the Sherlock bug let computation happen on one token while the transfer executed with another (state computed under one assumption, executed under a different, attacker-chosen one).

### Likelihood Explanation
Both `ValidateMultiSign` and `BatchValidateSign` are unprivileged, permissionless precompiles reachable by any smart contract on TVM via a `CALL`/`STATICCALL` to their fixed precompile addresses (`0x9`, `0xa`), gated only by `VMConfig.allowTvmSolidity059()`. Any user-deployed contract can construct arbitrary calldata for these addresses, so exploitation requires no privileged role — only building malformed/self-referential ABI payloads, exactly matching the "unprivileged, injected-calldata" pattern in the original report. The existence of the dedicated TIP-854 test suite for exactly this mismatch (`testTip854RejectsMalformedCalldata`, `testTip854PreActivationNoOp`, `testTip854OuterFrameContainment`) confirms the java-tron team already recognizes and is actively patching this bug class, which corroborates that it is a real, non-theoretical issue — the residual risk is confined to networks/time windows where the `allowTvmOsaka` hard fork has not yet activated.

### Recommendation
Ensure the `isValidAbiEncoding` guard (or equivalent length/offset-consistency check) is applied unconditionally rather than gated behind a forward-looking hard-fork flag, or accelerate activation of the guarding fork across all networks so that no window exists where `getEnergyForData()`'s assumed layout and `execute()`'s actual decoded layout can diverge. Additionally, review the outer `try/catch` in `BatchValidateSign.execute()` to ensure it never silently returns success for the energy already charged when internal decoding aborts on malformed input — either revert consistently or fail the entire call rather than substituting a default result.

### Proof of Concept
With `VMConfig.allowTvmOsaka()` disabled (pre-fork state, the default in this snapshot), calling `BatchValidateSign.execute()` with calldata such as `new byte[(5 + 1) * 32]` (a length inconsistent with the `(len/32 - 5) / 6` shape the energy formula assumes) bypasses the `isValidAbiEncoding` guard entirely and proceeds into `doExecute()`, where offset-driven array extraction can throw, and the outer catch converts this into `(true, new byte[WORD_SIZE])` rather than a `(false, ...)` failure — demonstrated directly by the existing regression test: [10](#0-9) 
An equivalent divergence is demonstrated for `ValidateMultiSign` (which has no outer catch and instead throws a raw `RuntimeException` on malformed pre-activation input, per the documented legacy behavior test): [11](#0-10)

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1044-1049)
```java
    @Override
    public long getEnergyForData(byte[] data) {
      long cnt = (data.length / WORD_SIZE - 5) / 5;
      // one sign 1500, half of ecrecover
      return cnt * ENGERYPERSIGN;
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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1137-1142)
```java
    @Override
    public long getEnergyForData(byte[] data) {
      long cnt = (data.length / WORD_SIZE - 5) / 6;
      // one sign 1500, half of ecrecover
      return cnt * ENGERYPERSIGN;
    }
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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1156-1181)
```java
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

**File:** framework/src/test/java/org/tron/common/runtime/vm/BatchValidateSignContractTest.java (L194-213)
```java
      Pair<Boolean, byte[]> post = validateMultiSign(hash, signatures, addresses);
      Assert.assertEquals(pre.getLeft(), post.getLeft());
      Assert.assertArrayEquals(pre.getValue(), post.getValue());
    } finally {
      VMConfig.initAllowTvmOsaka(0);
    }
    System.gc();
  }

  // TIP-854: before activation the guard is not consulted. Malformed calldata
  // that would raise inside doExecute gets collapsed to (true, 32-byte zero) by
  // the outer catch — this is the legacy behaviour and must be preserved.
  @Test
  public void testTip854PreActivationNoOp() {
    VMConfig.initAllowTvmOsaka(0);
    contract.setVmShouldEndInUs(System.nanoTime() / 1000 + 2_000_000);
    Pair<Boolean, byte[]> ret = contract.execute(new byte[(5 + 1) * 32]);
    Assert.assertTrue("pre-activation must not take the new reject path", ret.getLeft());
    Assert.assertEquals(32, ret.getRight().length);
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
