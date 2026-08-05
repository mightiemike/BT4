### Title
Unchecked native (low-level) call result hardcoded to `true` in `JLibrustzcash.librustzcashComputeNf` - (File: `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java`)

### Summary
The `swapOn1INCH` bug class — ignoring the success/failure signal of a low-level external call and letting execution proceed as if it succeeded — has a direct analog in java-tron's zk-SNARK nullifier-computation wrapper. `JLibrustzcash.librustzcashComputeNf` invokes the native JNI method `librustzcashSaplingComputeNf` but discards whatever the native layer reports and unconditionally returns `true`, even though every other sibling wrapper in the same class (`librustzcashComputeCm`, `librustzcashKaAgree`, `librustzcashCheckDiversifier`, etc.) correctly forwards the native boolean result. [1](#0-0) 

### Finding Description
Compare the two adjacent wrappers:

```java
public static boolean librustzcashComputeCm(ComputeCmParams params) {
  return INSTANCE.librustzcashSaplingComputeCm(params.getD(), params.getPkD(),
      params.getValue(), params.getR(), params.getCm());
}

public static boolean librustzcashComputeNf(ComputeNfParams params) {
  INSTANCE.librustzcashSaplingComputeNf(params.getD(), params.getPkD(), params.getValue(),
      params.getR(), params.getAk(), params.getNk(), params.getPosition(), params.getResult());
  return true;
}
``` [1](#0-0) 

`librustzcashComputeCm` forwards the boolean returned by the native `INSTANCE` call to its caller. `librustzcashComputeNf` calls the equivalent native method but throws away its return value entirely and hardcodes `true`. This is exactly the "unchecked low-level call" pattern in the report: the caller relies on the wrapper's boolean to decide whether the nullifier bytes written into `params.getResult()` are valid, but the wrapper can never signal failure regardless of what the native/JNI layer actually reports.

Every caller of this API is written defensively, expecting the wrapper to propagate failures:

- `Note.nullifier(...)` returns `null` on failure of `librustzcashComputeNf`. [2](#0-1) 
- `Wallet.createShieldNullifier` and `Wallet.getShieldedTRC20Nullifier` also branch on `!JLibrustzcash.librustzcashComputeNf(...)` to short-circuit and return `null`/reject. [3](#0-2) [4](#0-3) 

Because the wrapper always reports success, none of these `if (!JLibrustzcash.librustzcashComputeNf(...))` guards can ever trigger, even if the native computation genuinely fails (e.g., malformed diversifier/point that the native Rust code rejects, or any other native-side error condition that would normally be surfaced as `false`). The nullifier byte buffer would be used as-is, potentially uninitialized/garbage/zero, and propagated into wallet-facing nullifier APIs.

### Impact Explanation
The nullifier is the value used to prevent double-spending of a shielded note (it's ultimately checked against `NullifierStore`/inserted at spend time). If the native routine fails silently and the wrapper still reports success, downstream code (`Note.nullifier`, `Wallet.createShieldNullifier`, `Wallet.getShieldedTRC20Nullifier`) will treat a bad/garbage nullifier as valid. This affects wallet-side nullifier construction used in shielded transfer parameter building and TRC20 shielded-note-spent checks. The severity is bounded because this path is invoked in wallet/API tooling around shielded-note handling rather than directly in on-chain consensus validation of spends (the consensus-critical checks in `PrecompiledContracts`/`ShieldedTransferActuator` use `librustzcashSaplingCheckSpend`/`FinalCheck`, which are separate and correctly checked functions), but it is still a genuine "unchecked low-level call" defect matching the report's bug class, capable of masking native-layer failures in nullifier computation used by wallet/API and TRC20 shielded flows.

### Likelihood Explanation
The defect is unconditionally present on every call to `librustzcashComputeNf`/`Note.nullifier(...)` — there is no special configuration needed to trigger the code path; only a native-layer failure (which the wrapper is specifically designed to catch and does not) is needed to demonstrate the missed-check behavior. This is directly reachable by unprivileged users calling shielded wallet/API endpoints (`createShieldNullifier`, `isShieldedTRC20ContractNoteSpent` path) that ultimately call this function.

### Recommendation
Fix `librustzcashComputeNf` to propagate the native call's actual boolean result instead of hardcoding `true`, consistent with `librustzcashComputeCm` and the other sibling wrappers:

```java
public static boolean librustzcashComputeNf(ComputeNfParams params) {
  return INSTANCE.librustzcashSaplingComputeNf(params.getD(), params.getPkD(), params.getValue(),
      params.getR(), params.getAk(), params.getNk(), params.getPosition(), params.getResult());
}
```

### Proof of Concept
1. Call `Note.nullifier(ak, nk, position)` (or `Wallet.createShieldNullifier`) with valid-looking-but-native-rejected parameters that would cause the underlying `librustzcashSaplingComputeNf` JNI call to return `false` internally.
2. Because `JLibrustzcash.librustzcashComputeNf` ignores the return value and always returns `true`, the caller's `if (!JLibrustzcash.librustzcashComputeNf(...)) return null;` guard never fires.
3. The (potentially invalid/uninitialized) `result` buffer is returned to the caller as if it were a legitimately-computed nullifier, whereas the intended behavior (mirrored by `librustzcashComputeCm`) is to signal failure and abort. [5](#0-4)

### Citations

**File:** chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java (L60-69)
```java
  public static boolean librustzcashComputeCm(ComputeCmParams params) {
    return INSTANCE.librustzcashSaplingComputeCm(params.getD(), params.getPkD(),
        params.getValue(), params.getR(), params.getCm());
  }

  public static boolean librustzcashComputeNf(ComputeNfParams params) {
    INSTANCE.librustzcashSaplingComputeNf(params.getD(), params.getPkD(), params.getValue(),
        params.getR(), params.getAk(), params.getNk(), params.getPosition(), params.getResult());
    return true;
  }
```

**File:** framework/src/main/java/org/tron/core/zen/note/Note.java (L196-214)
```java
  public byte[] nullifier(FullViewingKey vk, long position) throws ZksnarkException {
    byte[] ak = vk.getAk();
    byte[] nk = vk.getNk();
    byte[] result = new byte[32]; // 256
    if (!JLibrustzcash.librustzcashComputeNf(
        new ComputeNfParams(d.getData(), pkD, value, rcm, ak, nk, position, result))) {
      return null;
    }
    return result;
  }

  public byte[] nullifier(byte[] ak, byte[] nk, long position) throws ZksnarkException {
    byte[] result = new byte[32]; // 256
    if (!JLibrustzcash.librustzcashComputeNf(
        new ComputeNfParams(d.getData(), pkD, value, rcm, ak, nk, position, result))) {
      return null;
    }
    return result;
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2734-2750)
```java
    ComputeNfParams computeNfParams = new ComputeNfParams(
        paymentAddress.getD().getData(),
        paymentAddress.getPkD(),
        note.getValue(),
        note.getRcm().toByteArray(),
        ak,
        nk,
        incrementalMerkleVoucherContainer.position(),
        result);
    if (!JLibrustzcash.librustzcashComputeNf(computeNfParams)) {
      return null;
    }

    return BytesMessage.newBuilder()
        .setValue(ByteString.copyFrom(result))
        .build();
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L4175-4197)
```java
  private byte[] getShieldedTRC20Nullifier(GrpcAPI.Note note, long pos, byte[] ak,
      byte[] nk) throws ZksnarkException {
    byte[] result = new byte[32]; // 256
    PaymentAddress paymentAddress = KeyIo.decodePaymentAddress(
        note.getPaymentAddress());
    if (Objects.isNull(paymentAddress)) {
      throw new ZksnarkException(PAYMENT_ADDRESS_FORMAT_WRONG);
    }

    ComputeNfParams computeNfParams = new ComputeNfParams(
        paymentAddress.getD().getData(),
        paymentAddress.getPkD(),
        note.getValue(),
        note.getRcm().toByteArray(),
        ak,
        nk,
        pos,
        result);
    if (!JLibrustzcash.librustzcashComputeNf(computeNfParams)) {
      return null;
    }
    return result;
  }
```
