## Title
`ValidateMultiSign` TVM precompiled contract lacks input validation, causing an uncaught `ArrayIndexOutOfBoundsException` on malformed calldata - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
The `ValidateMultiSign` precompiled contract (address `0x...a`) parses raw TVM calldata into `DataWord[]` and immediately dereferences fixed offsets (`words[0]`, `words[1]`, `words[2]`, `words[3]`, and `words[words[3].intValueSafe()/WORD_SIZE]`) before any bounds checking, and before entering its only `try/catch` block. If a caller (any smart contract, reachable from any account via a broadcast transaction or a `TriggerConstantContract`/`TriggerSmartContract` call) supplies calldata shorter than the expected header, this decoding throws an unhandled `ArrayIndexOutOfBoundsException`/`RuntimeException` that propagates out of `execute()`, unlike the analogous `PlonkVerifier::verify_gnark_proof().unwrap()` panic in the reference report.

### Finding Description
`ValidateMultiSign.execute()` is defined in [1](#0-0)  as:

```java
public Pair<Boolean, byte[]> execute(byte[] rawData) {
  if (VMConfig.allowTvmOsaka() && !isValidAbiEncoding(rawData, ABI_HEADER_WORDS, ABI_ITEM_WORDS)) {
    return Pair.of(false, EMPTY_BYTE_ARRAY);
  }
  DataWord[] words = DataWord.parseArray(rawData);
  byte[] address = words[0].toTronAddress();
  int permissionId = words[1].intValueSafe();
  byte[] data = words[2].getData();

  byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
  byte[] hash = Sha256Hash.hash(...);

  if (VMConfig.allowTvmSelfdestructRestriction()) {
    int sigArraySize = words[words[3].intValueSafe() / WORD_SIZE].intValueSafe();
    ...
  }
  byte[][] signatures = ... extractSigArray(words, words[3].intValueSafe() / WORD_SIZE, rawData) ...
  ...
  try {
    Permission permission = account.getPermissionById(permissionId);
    ...
  } catch (Throwable t) { ... }
  return Pair.of(true, DATA_FALSE);
}
```

Only the ABI-encoding guard `isValidAbiEncoding(...)` is conditioned on `VMConfig.allowTvmOsaka()` (a hard-fork/feature flag introduced by TIP-854). Before that flag is activated (or for any node/feature-flag configuration where it is not yet enabled), `rawData` is fed directly to `DataWord.parseArray` and the fixed-offset word accesses `words[0]`..`words[3]` and the derived index `words[words[3].intValueSafe()/WORD_SIZE]` with **no length check**. All of this parsing sits **outside** the method's only `try { ... } catch (Throwable t)` block, which wraps only the `account.getPermissionById(...)` weight-checking logic further down.

This is confirmed by the project's own test suite comment in [2](#0-1) , which explicitly documents: *"before activation, malformed calldata reaches the legacy decoder... this precompile has no outer catch, so a too-short input raises inside the decoder; that is the documented pre-activation failure mode the TIP explicitly preserves."* The test wraps the call in a `try/catch (RuntimeException expectedLegacyBehaviour)` specifically because the production code is known to throw.

By contrast, the sibling precompile `BatchValidateSign` wraps its entire `doExecute(data)` in a top-level `try { ... } catch (Throwable t) { return Pair.of(true, new byte[WORD_SIZE]); }` (see [3](#0-2) ), demonstrating the intended defensive pattern that `ValidateMultiSign` is missing for its pre-Osaka code path.

### Impact Explanation
`ValidateMultiSign` is a public precompiled contract reachable from **any** TVM contract call — i.e., any account can trigger it via a normal `TriggerSmartContract`/`TriggerConstantContract` RPC call or via a deployed contract executed inside a broadcast transaction, exactly matching the "anonymous RPC request / broadcast transaction / contract call" reachability required. Supplying calldata shorter than the fixed header (fewer than 4 words) causes an unhandled exception to propagate out of the precompile's `execute()` method during TVM execution. Depending on how the exception is caught further up the call stack (opcode dispatch / `Program`/`OperationActions`), this can manifest as:
- Constant/estimate-energy calls (`TriggerConstantContract`, `EstimateEnergy`) throwing back to the RPC layer instead of gracefully returning a revert, which is a DoS vector against any node exposing that public RPC/HTTP endpoint.
- Non-deterministic handling if different node versions/paths catch this exception differently while others don't, risking chain processing inconsistency for transactions that exercise this path in non-constant execution.

This is a lower-severity, narrower analog than the reported bug (Java catches exceptions in surrounding VM machinery in most execution paths, unlike a Rust panic which can abort a whole process), but it is a genuine unhandled-exception-on-malformed-input defect in a public, permissionless TVM precompile — matching the "DoS via ... protocol implementation" acceptance criterion.

### Likelihood Explanation
High likelihood of triggering: any account can call the `ValidateMultiSign` precompile address directly with arbitrary short calldata via a contract `CALL`, with zero privilege required. The only guard (`isValidAbiEncoding`) is gated behind the `allowTvmOsaka` feature flag, so any deployment/node state where that flag is not yet activated is exposed. The project's own tests acknowledge and preserve this exact failure mode for the pre-activation path, confirming the vulnerability is real and known but only partially mitigated (post-activation only).

### Recommendation
- Apply the same unconditional length/structure validation performed by `isValidAbiEncoding` regardless of the `allowTvmOsaka` flag, or add an explicit early bounds check (`rawData.length >= (ABI_HEADER_WORDS) * WORD_SIZE` and validate `words[3]`'s derived index before use) at the top of `ValidateMultiSign.execute()`.
- Wrap the entire `execute()` body (as `BatchValidateSign.execute()` already does) in a top-level `try { ... } catch (Throwable t) { return Pair.of(true, DATA_FALSE); }` so any decoding failure degrades to a deterministic "signature invalid" result rather than propagating an exception.
- Add explicit bounds checks on `words[3].intValueSafe() / WORD_SIZE` before using it as an array index, mirroring the defensive checks already used in `extractSigArray`/`extractBytesArray`.
- Add fuzz/unit tests analogous to `testTip854RejectsMalformedCalldata` (already present for `BatchValidateSign`) for `ValidateMultiSign` to assert malformed calldata is rejected deterministically under both flag states, not just post-activation.

### Proof of Concept
1. Deploy a contract (or use `TriggerConstantContract`/`TriggerSmartContract`) that performs a `CALL` to precompile address `0x000000000000000000000000000000000000000000000000000000000000000a` (`validateMultiSignAddr`).
2. Supply calldata shorter than 4 words (e.g., 32 or 64 bytes) with `VMConfig.allowTvmOsaka()` not yet active (pre-TIP-854 state), matching the reproduction already codified in [4](#0-3) .
3. Observe `DataWord.parseArray(rawData)` / `words[0].toTronAddress()` / `words[3].intValueSafe()` throw an uncaught `RuntimeException`/`ArrayIndexOutOfBoundsException` out of `ValidateMultiSign.execute()`, since no guard or catch block exists ahead of the account-permission `try` block, as shown in [5](#0-4) .

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1036-1076)
```java
  public static class ValidateMultiSign extends PrecompiledContract {

    private static final int ENGERYPERSIGN = 1500;
    private static final int MAX_SIZE = 5;
    private static final int ABI_HEADER_WORDS = 5;
    private static final int ABI_ITEM_WORDS = 5;


    @Override
    public long getEnergyForData(byte[] data) {
      long cnt = (data.length / WORD_SIZE - 5) / 5;
      // one sign 1500, half of ecrecover
      return cnt * ENGERYPERSIGN;
    }

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

      if (signatures.length == 0 || signatures.length > MAX_SIZE) {
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
