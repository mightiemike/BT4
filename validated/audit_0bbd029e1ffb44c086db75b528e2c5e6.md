### Title
Missing length validation on `ExpandedSpendingKey.ask` before native `librustzcashSaplingSpendSig` call - (`ShieldedTRC20ParametersBuilder.java`)

### Summary
`ShieldedTRC20ParametersBuilder.createSpendAuth` passes `spends.get(i).expsk.getAsk()` directly into `JLibrustzcash.librustzcashSaplingSpendSig` with no length check, and the `JLibrustzcash` wrapper for this specific native call also performs no size validation, unlike sibling wrapper methods in the same class.

### Finding Description
`createSpendAuth` iterates over `spends` and, for each entry, calls `JLibrustzcash.librustzcashSaplingSpendSig` with `spends.get(i).expsk.getAsk()`, `alpha`, and `dataToBeSigned` directly, only checking that `expsk` is non-null, not that `ask` has the expected 32-byte length: [1](#0-0) 

`JLibrustzcash.librustzcashSaplingSpendSig` forwards `params.getAsk()`, `params.getAlpha()`, `params.getSigHash()` straight to the native JNI method with no size validation: [2](#0-1) 

This is notably inconsistent with other wrapper methods in the same file that do validate fixed-size buffers before calling into native code, e.g. `librustzcashAskToAk` and `librustzcashNskToNk` both call `LibrustzcashParam.valid32Params(...)` before invoking the native function: [3](#0-2) 

So there is a real, demonstrable gap: `ask` (and `alpha`/`sigHash`) reaching `librustzcashSaplingSpendSig` are not asserted to be fixed-size 32/64-byte arrays before the JNI boundary, unlike comparable native-call wrappers in the same class.

However, I could not confirm within the available index whether this method is reachable from an *unprivileged, remote* RPC caller with a fully attacker-controlled `ExpandedSpendingKey` (ask/nsk/ovk). The gRPC path that is clearly reachable by ordinary callers, `createShieldedContractParametersWithoutAsk`, builds spends via `addSpend(ak, nsk, note, ...)` (no `ask` field at all — signing is deferred to a separate `createSpendAuthSig` call), which does not go through `createSpendAuth`/`build(true)`. The `build(true)` / `createSpendAuth` path is used by `addSpend(ExpandedSpendingKey, ...)`, which requires the full `ExpandedSpendingKey` including `ask` — this is exercised in test code (`ShieldedTRC20BuilderTest.java`) but I was not able to fully verify, before running out of iterations, whether a corresponding gRPC/HTTP endpoint in `Wallet.java` accepts a raw attacker-supplied `ask` byte array of arbitrary length and forwards it to `build(true)` without any prior length assertion (e.g., in `TransferService`/`Wallet.createShieldedContractParameters`). This is the missing piece needed to fully confirm remote unauthenticated reachability.

### Impact Explanation
If reachable, the impact is a native-layer crash/undefined behavior (out-of-bounds read in the Rust/C `librustzcash` library) triggered by a JNI call with a malformed fixed-size buffer, i.e., a DoS via RPC-API against the node process. It is not an authorization bypass — the signature scheme itself is cryptographically sound; an attacker forging `ask`/`nsk`/`ovk` cannot produce a valid signature for someone else's nullifier/anchor, since spend proof and downstream `librustzcashSaplingCheckSpend` verification (elsewhere) would reject a spend authorized with the wrong key. The only credible scoped impact is the missing length validation causing native crash, not fund theft or authorization bypass.

### Likelihood Explanation
Contingent on confirming an actual RPC/HTTP entry point that lets an unprivileged, unauthenticated caller supply a raw `ExpandedSpendingKey.ask` (or `alpha`/`sigHash`) of non-standard length that flows unchecked into `build(true)` → `createSpendAuth` → `librustzcashSaplingSpendSig`. This could not be fully verified with available tools/index coverage for `Wallet.java`. If such a path exists, the attacker cost is trivial (a single malformed gRPC/HTTP request), and it would be fully repeatable.

### Recommendation
Add explicit length validation (`LibrustzcashParam.valid32Params`/`validParamLength`) for `ask` (32 bytes), `alpha` (32 bytes), and `dataToBeSigned`/`sigHash` (32 bytes) inside `JLibrustzcash.librustzcashSaplingSpendSig` (mirroring `librustzcashAskToAk`/`librustzcashNskToNk`), and/or add an explicit check in `ShieldedTRC20ParametersBuilder.createSpendAuth` that `expsk.getAsk().length == 32` before calling into the native layer, throwing `ZksnarkException` otherwise.

### Proof of Concept
```java
@Test
public void createSpendAuthRejectsMalformedAsk() throws Exception {
  ShieldedTRC20ParametersBuilder builder = new ShieldedTRC20ParametersBuilder();
  // ... configure a TRANSFER-type builder with one spend ...
  ExpandedSpendingKey forgedExpsk = new ExpandedSpendingKey(
      new byte[10], /* malformed ask */
      validNsk,
      validOvk);
  builder.addSpend(forgedExpsk, note, anchor, path, position);
  // expect ZksnarkException raised in Java layer BEFORE native invocation
  Assert.assertThrows(ZksnarkException.class, () -> builder.build(true));
}
```
Note: this PoC demonstrates the missing validation at the `ShieldedTRC20ParametersBuilder`/`JLibrustzcash` layer; confirming end-to-end remote exploitability requires verifying the exact `Wallet.java`/gRPC entry point that accepts a raw `ExpandedSpendingKey` from an unauthenticated caller, which was not fully verifiable within this session.

### Citations

**File:** framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java (L219-232)
```java
  private void createSpendAuth(byte[] dataToBeSigned) throws ZksnarkException {
    for (int i = 0; i < spends.size(); i++) {
      if (spends.get(i).expsk == null) {
        throw new ZksnarkException("missing expanded spending key for spend authorization");
      }
      byte[] result = new byte[64];
      JLibrustzcash.librustzcashSaplingSpendSig(
          new LibrustzcashParam.SpendSigParams(spends.get(i).expsk.getAsk(),
              spends.get(i).alpha,
              dataToBeSigned,
              result));
      builder.getSpendDescriptionBuilder(i)
          .setSpendAuthoritySignature(ByteString.copyFrom(result));
    }
```

**File:** chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java (L71-91)
```java
  /**
   * @param ask the spend authorizing key,to generate ak, 32 bytes
   * @return ak 32 bytes
   */
  public static byte[] librustzcashAskToAk(byte[] ask) throws ZksnarkException {
    LibrustzcashParam.valid32Params(ask);
    byte[] ak = new byte[32];
    INSTANCE.librustzcashAskToAk(ask, ak);
    return ak;
  }

  /**
   * @param nsk the proof authorizing key, to generate nk, 32 bytes
   * @return 32 bytes
   */
  public static byte[] librustzcashNskToNk(byte[] nsk) throws ZksnarkException {
    LibrustzcashParam.valid32Params(nsk);
    byte[] nk = new byte[32];
    INSTANCE.librustzcashNskToNk(nsk, nk);
    return nk;
  }
```

**File:** chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java (L136-139)
```java
  public static boolean librustzcashSaplingSpendSig(SpendSigParams params) {
    return INSTANCE.librustzcashSaplingSpendSig(params.getAsk(), params.getAlpha(),
        params.getSigHash(), params.getResult());
  }
```
