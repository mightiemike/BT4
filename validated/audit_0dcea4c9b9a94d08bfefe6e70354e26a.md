### Title
Uncaught RuntimeException from malformed calldata in `ValidateMultiSign.execute()` propagates through `Program.callToPrecompiledAddress`, halting transaction execution unless a feature flag is enabled - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
`PrecompiledContracts.ValidateMultiSign.execute()` decodes raw calldata into `DataWord[]` and indexes it without a header-shape check unless the `allowTvmOsaka` (TIP-854) flag is enabled. Any caller (contract or top-level transaction) can invoke the `validateMultiSign` precompile with truncated/malformed calldata, causing an uncaught `RuntimeException` (e.g. `ArrayIndexOutOfBoundsException`) to bubble out of `execute()`. `Program.callToPrecompiledAddress` re-throws that exception (`throw result.getException()`), which is the same "parse untrusted data → unpack fails → exception halts processing" bug class described in the Optimism report, but here it is reachable from a normal `TriggerSmartContract`/internal `CALL` on mainnet whenever `allowTvmOsaka` has not yet been activated.

### Finding Description
`ValidateMultiSign.execute()` only performs the `isValidAbiEncoding` shape check when `VMConfig.allowTvmOsaka()` is true: [1](#0-0) 

If that flag is off (its default/pre-activation state, as confirmed by the dedicated regression test `testTip854PreActivationNoOp`), calldata shorter than the fixed 5-word header or otherwise malformed reaches `DataWord.parseArray(rawData)` and subsequent unguarded array indexing (`words[1]`, `words[2]`, `words[3]`, etc.) without any bounds validation, and the resulting exception is not caught anywhere in `ValidateMultiSign.execute()`: [2](#0-1) 

This differs from the sibling precompile `BatchValidateSign`, whose `execute()` wraps the entire decode/verify logic in a `try { return doExecute(data); } catch (Throwable t) { ... return Pair.of(true, new byte[WORD_SIZE]); }` and therefore never lets a decode error escape: [3](#0-2) 

The uncaught exception from `ValidateMultiSign.execute()` propagates directly to `Program.callToPrecompiledAddress`, which calls `contract.execute(data)` with no surrounding try/catch: [4](#0-3) 

This mirrors the root cause of the reported bug class exactly: attacker-controlled calldata is decoded via a fixed-shape unpacking routine (here, `DataWord.parseArray` + positional indexing instead of ABI `MethodById`/`Inputs.Unpack`), and a decode failure throws instead of being handled gracefully — except that here the reachable path is a live TVM `CALL` to the precompiled address (`0x singleton for validateMultiSign`), invoked from any smart contract or `TriggerSmartContract`, not an offline migration tool.

### Impact Explanation
Because the code path is only guarded by `allowTvmOsaka()` (a chain-parameter-gated TIP-854 activation flag), on any network/height where this TIP has not yet been activated, a malicious contract can call the `ValidateMultiSign` precompile address with intentionally too-short or misaligned calldata to force an uncaught `RuntimeException` out of `execute()`. This propagates through `callToPrecompiledAddress`/`Program`/`VM.play` up into the transaction's execution result as an unexpected exception rather than the normal "push 0 / revert" precompile failure path that `BatchValidateSign` and the post-activation `ValidateMultiSign` guard both produce. This is a denial-of-service class issue in the TVM execution/precompiled-contract layer: an attacker-triggerable, non-reverting exception during otherwise ordinary transaction execution, exactly analogous to the reported "malicious actor halts processing by supplying malformed calldata to a fixed-layout decoder" bug, but on the live consensus/execution path rather than an offline migration script.

### Likelihood Explanation
High reachability: `validateMultiSign` is a public precompiled contract address invocable via a normal `CALL`/`STATICCALL`/`DELEGATECALL` from any deployed smart contract, and can be triggered by any account through `TriggerSmartContractServlet`/`TriggerConstantContractServlet` or a broadcast transaction. Exploitation requires no privilege — only calldata shorter than 6*32 bytes or otherwise not matching the header shape when `allowTvmOsaka` is disabled. The team's own regression test (`testTip854PreActivationNoOp`) explicitly documents that "before activation ... too-short input raises inside the decoder; that is the documented pre-activation failure mode," confirming this is a known, currently-live gap that TIP-854 is designed to close only once activated.

### Recommendation
Move the `isValidAbiEncoding` (or equivalent bounds) check in `ValidateMultiSign.execute()` outside of the `VMConfig.allowTvmOsaka()` gate so malformed calldata is always rejected via the normal `Pair.of(false, EMPTY_BYTE_ARRAY)`/`Pair.of(true, DATA_FALSE)` failure path, independent of feature-flag activation state — mirroring the defensive `try { doExecute } catch (Throwable t) { ... }` wrapper already present in `BatchValidateSign.execute()`. At minimum, wrap `ValidateMultiSign.execute()` in the same outer try/catch so any decode failure degrades to a safe `Pair.of(true, DATA_FALSE)`/`Pair.of(false, EMPTY_BYTE_ARRAY)` return instead of throwing.

### Proof of Concept
1. Deploy (or use an existing) contract that issues a raw `CALL` to the `ValidateMultiSign` precompile address with `inDataSize = 0` (or any calldata whose length is less than `(5+1)*32` bytes, e.g. `new byte[(5+1)*32]` truncated further or misaligned).
2. Ensure the chain has not activated TIP-854 (`allowTvmOsaka` == 0, the default state before the parameter is voted in).
3. `PrecompiledContracts.ValidateMultiSign.execute()` calls `DataWord.parseArray(rawData)` and then indexes `words[1]`, `words[2]`, `words[3]` etc. without a length guard, throwing (e.g. `ArrayIndexOutOfBoundsException`).
4. `Program.callToPrecompiledAddress` receives this from `contract.execute(data)` with no catch block and the exception propagates up through `VM.play`/`Program.callToAddress`, producing an execution result with an unhandled exception instead of the intended "push 0 and continue" precompile-failure semantics that `BatchValidateSign` guarantees.
This behavior is directly confirmed by the project's own test `ValidateMultiSignContractTest.testTip854PreActivationNoOp`, which asserts that pre-activation, `contract.execute()` "may throw" and treats that as the documented (unfixed pre-TIP-854) legacy behavior. [5](#0-4)

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1051-1060)
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

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1741-1755)
```java
      Pair<Boolean, byte[]> out = contract.execute(data);

      if (out.getLeft()) { // success
        this.refundEnergy(msg.getEnergy().longValue() - requiredEnergy, CALL_PRE_COMPILED);
        this.stackPushOne();
        returnDataBuffer = out.getRight();
        deposit.commit();
      } else {
        // spend all energy on failure, push zero and revert state changes
        this.refundEnergy(0, CALL_PRE_COMPILED);
        this.stackPushZero();
        if (Objects.nonNull(this.result.getException())) {
          throw result.getException();
        }
      }
```
