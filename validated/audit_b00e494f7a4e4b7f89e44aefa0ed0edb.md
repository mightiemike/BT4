### Title
Missing length validation on attacker-controlled `ak`/`nsk`/`rcm` bytes before native zk-proof JNI call in `ZenTransactionBuilder.generateSpendProof`/`generateOutputProof` (reached via `buildWithoutAsk`) - (File: framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java)

### Summary
`ZenTransactionBuilder.buildWithoutAsk` → `build(false)` calls `generateSpendProof` and `generateOutputProof`, which forward attacker-controlled byte arrays (`ak`, `nsk`, `rcm`/`r`, `alpha`, `voucherPath`) directly into the JNI-bound native `librustzcashSaplingSpendProof`/`librustzcashSaplingOutputProof` calls without validating their lengths, unlike several other native call sites in `JLibrustzcash` that do call `LibrustzcashParam.valid32Params`/`validParamLength` first. Only `ovk` length is checked (32 bytes) in `generateOutputProof`; `ak`, `nsk`, and `rcm` are not.

### Finding Description
`Wallet.createShieldedTransactionWithoutSpendAuthSig` (which is exposed via the shielded transaction gRPC/HTTP API guarded only by `checkAllowShieldedTransactionApi()`, not by any privileged role) takes raw protobuf bytes for `ak`, `nsk`, `ovk` from `PrivateParametersWithoutAsk` and only checks `ArrayUtils.isEmpty(...)`, never their exact byte length: [1](#0-0) 

Likewise, note `rcm` bytes come straight from the request’s `SpendNote`/`ReceiveNote` protobuf field via `note.getRcm().toByteArray()` with no length check: [2](#0-1) 

These attacker-supplied byte arrays are placed into `SpendDescriptionInfo` and eventually consumed by `ZenTransactionBuilder.generateSpendProof`, which passes `ak`, `nsk`, `spend.note.getRcm()`, `spend.alpha`, `spend.anchor`, and `voucherPath` directly into `JLibrustzcash.librustzcashSaplingSpendProof` with **no length validation**: [3](#0-2) 

Similarly, `generateOutputProof` only validates `ovk.length != 32`, but not `output.getNote().getRcm()`/`pkD`/D data before calling `librustzcashSaplingOutputProof`: [4](#0-3) 

This contrasts with other native entry points in `JLibrustzcash` that explicitly enforce fixed-size buffers before crossing the JNI boundary (e.g. `librustzcashAskToAk`, `librustzcashNskToNk`, `librustzcashSaplingGenerateR`, `librustzcashToScalar`, `librustzcashTreeUncommitted` all call `LibrustzcashParam.valid32Params`/`validParamLength`), but `librustzcashSaplingSpendProof`/`librustzcashSaplingOutputProof` in `JLibrustzcash` perform no such check: [5](#0-4) 

Because the Rust/JNI native layer (`Librustzcash` via JNA/JNI bridge) expects fixed-size C buffers (e.g. 32-byte `ak`/`nsk`/`rcm`, fixed-length `voucherPath`), passing a Java `byte[]` of a different length than the native function expects to read/write is unsafe: the native function will read/write based on its own hard-coded expected size or a length parameter, and if the JVM-side array is shorter than expected, the native code can read out-of-bounds JVM heap memory (or write past bounds when returning results), which for JNI/JNA-backed native calls typically manifests as a JVM crash (SIGSEGV) rather than a graceful exception, since Java-side length checks that would normally catch such mismatches are missing here.

### Impact Explanation
An unprivileged, funded account (or a client with access to the shielded-transaction API) can submit a `PrivateParametersWithoutAsk` request with an `ak`, `nsk`, or note `rcm` field whose byte length differs from the 32 bytes the native Rust FFI expects. This reaches `ZenTransactionBuilder.buildWithoutAsk` → `generateSpendProof`/`generateOutputProof` → `JLibrustzcash.librustzcashSaplingSpendProof`/`OutputProof` with no prior length validation, risking an out-of-bounds native memory access. Depending on the underlying native binding this can crash the node process (denial of service) or, in the worst case, corrupt adjacent native memory. This matches the "Node RCE / crash" bounty impact class, at minimum a DoS.

### Likelihood Explanation
Preconditions: shielded transaction API must be enabled (`checkAllowShieldedTransactionApi`), which is standard for nodes supporting shielded transfers; no signature, key, or special role is required to hit `createShieldedTransactionWithoutSpendAuthSig`. The attacker only needs to craft a gRPC/HTTP request with malformed-length `ak`/`nsk`/`rcm` fields — no on-chain fee is paid at this stage since the crash occurs during transaction *construction*, before broadcast/fee deduction. This makes the attack essentially free and repeatable against any node exposing this API.

### Recommendation
Add explicit length validation (e.g., via `LibrustzcashParam.valid32Params`/`validParamLength`, matching the pattern already used for `librustzcashAskToAk`/`librustzcashNskToNk`) for `ak`, `nsk`, `rcm`/`r`, `alpha`, and `voucherPath` in `Wallet.createShieldedTransactionWithoutSpendAuthSig` and inside `ZenTransactionBuilder.generateSpendProof`/`generateOutputProof` before any native/JNI call, rejecting requests with a `ContractValidateException`/`ZksnarkException` if lengths don't match expected fixed sizes.

### Proof of Concept
```java
// JUnit-style PoC illustrating missing pre-call length validation
@Test
public void testOversizedAkNskCausesUncheckedNativeCall() {
  PrivateParametersWithoutAsk.Builder req = PrivateParametersWithoutAsk.newBuilder();
  req.setAk(ByteString.copyFrom(new byte[1024]));   // should be 32 bytes
  req.setNsk(ByteString.copyFrom(new byte[1]));     // should be 32 bytes
  req.setOvk(ByteString.copyFrom(new byte[32]));
  // ... add one SpendNote with note.rcm of wrong length, e.g. new byte[4]

  Wallet wallet = new Wallet();
  try {
    wallet.createShieldedTransactionWithoutSpendAuthSig(req.build());
    Assert.fail("Expected rejection for malformed ak/nsk length before native call");
  } catch (ContractValidateException | ZksnarkException e) {
    // EXPECTED with a fix: reject here.
    // CURRENT BEHAVIOR: no such length check exists prior to
    // JLibrustzcash.librustzcashSaplingSpendProof in
    // ZenTransactionBuilder.generateSpendProof, so execution proceeds
    // to the native call with mismatched buffer sizes.
  }
}
```
Note: the actual native crash cannot be triggered/observed purely via the Java-level index in this analysis (the JNI/Rust binding implementation itself is outside indexed Java source), so full confirmation of memory corruption vs. safe native-side bounds checking requires running this PoC against a built node with the native `librustzcash` library loaded.

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2385-2392)
```java
    byte[] ak = request.getAk().toByteArray();
    byte[] nsk = request.getNsk().toByteArray();
    byte[] ovk = request.getOvk().toByteArray();

    if (ArrayUtils.isEmpty(transparentFromAddress) && (ArrayUtils.isEmpty(ak) || ArrayUtils
        .isEmpty(nsk) || ArrayUtils.isEmpty(ovk))) {
      throw new ContractValidateException("No input address");
    }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2446-2459)
```java
          Note baseNote = new Note(paymentAddress.getD(),
              paymentAddress.getPkD(), note.getValue(), note.getRcm().toByteArray());

          IncrementalMerkleVoucherContainer voucherContainer =
              new IncrementalMerkleVoucherCapsule(
                  spendNote.getVoucher()).toMerkleVoucherContainer();
          builder.addSpend(ak,
              nsk,
              ovk,
              baseNote,
              spendNote.getAlpha().toByteArray(),
              spendNote.getVoucher().getRt().toByteArray(),
              voucherContainer);
        }
```

**File:** framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java (L210-254)
```java
  // Note: should call librustzcashSaplingProvingCtxFree in the caller
  public SpendDescriptionCapsule generateSpendProof(SpendDescriptionInfo spend,
      long ctx) throws ZksnarkException {

    byte[] cm = spend.note.cm();

    // check if ak exists
    byte[] ak;
    byte[] nf;
    byte[] nsk;
    if (!ArrayUtils.isEmpty(spend.ak)) {
      ak = spend.ak;
      nf = spend.note.nullifier(ak, JLibrustzcash.librustzcashNskToNk(spend.nsk),
          spend.voucher.position());
      nsk = spend.nsk;
    } else {
      ak = spend.expsk.fullViewingKey().getAk();
      nf = spend.note.nullifier(spend.expsk.fullViewingKey(), spend.voucher.position());
      nsk = spend.expsk.getNsk();
    }

    if (ByteArray.isEmpty(cm) || ByteArray.isEmpty(nf)) {
      throw new ZksnarkException("Spend is invalid");
    }

    byte[] voucherPath = spend.voucher.path().encode();

    byte[] cv = new byte[32];
    byte[] rk = new byte[32];
    byte[] zkproof = new byte[192];
    if (!JLibrustzcash.librustzcashSaplingSpendProof(
        new SpendProofParams(ctx,
            ak,
            nsk,
            spend.note.getD().getData(),
            spend.note.getRcm(),
            spend.alpha,
            spend.note.getValue(),
            spend.anchor,
            voucherPath,
            cv,
            rk,
            zkproof))) {
      throw new ZksnarkException("Spend proof failed");
    }
```

**File:** framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java (L264-297)
```java
  // Note: should call librustzcashSaplingProvingCtxFree in the caller
  public ReceiveDescriptionCapsule generateOutputProof(ReceiveDescriptionInfo output, long ctx)
      throws ZksnarkException {
    byte[] cm = output.getNote().cm();
    if (ByteArray.isEmpty(cm)) {
      throw new ZksnarkException("Output is invalid");
    }

    Optional<NotePlaintextEncryptionResult> res = output.getNote()
        .encrypt(output.getNote().getPkD());
    if (!res.isPresent()) {
      throw new ZksnarkException("Failed to encrypt note");
    }

    NotePlaintextEncryptionResult enc = res.get();
    NoteEncryption encryptor = enc.getNoteEncryption();

    byte[] cv = new byte[32];
    byte[] zkProof = new byte[192];
    if (!JLibrustzcash.librustzcashSaplingOutputProof(
        new OutputProofParams(ctx,
            encryptor.getEsk(),
            output.getNote().getD().getData(),
            output.getNote().getPkD(),
            output.getNote().getRcm(),
            output.getNote().getValue(),
            cv,
            zkProof))) {
      throw new ZksnarkException("Output proof failed");
    }

    if (ArrayUtils.isEmpty(output.ovk) || output.ovk.length != 32) {
      throw new ZksnarkException("ovk is null or invalid and ovk should be 32 bytes (256 bit)");
    }
```

**File:** chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java (L123-134)
```java
  public static boolean librustzcashSaplingSpendProof(SpendProofParams params) {
    return INSTANCE.librustzcashSaplingSpendProof(params.getCtx(), params.getAk(),
        params.getNsk(), params.getD(), params.getR(), params.getAlpha(), params.getValue(),
        params.getAnchor(), params.getVoucherPath(), params.getCv(), params.getRk(),
        params.getZkproof());
  }

  public static boolean librustzcashSaplingOutputProof(OutputProofParams params) {
    return INSTANCE.librustzcashSaplingOutputProof(params.getCtx(), params.getEsk(),
        params.getD(), params.getPkD(), params.getR(), params.getValue(), params.getCv(),
        params.getZkproof());
  }
```
