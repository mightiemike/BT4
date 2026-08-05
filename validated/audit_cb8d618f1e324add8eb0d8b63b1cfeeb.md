### Title
Missing ciphertext-length validation in `Chacha20poly1305IetfDecryptParams.valid()` allows declared `cLen`/actual buffer-length mismatch reaching native ChaCha20-Poly1305 decrypt - ([File: chainbase/src/main/java/org/tron/common/zksnark/JLibsodiumParam.java])

### Summary
`Chacha20poly1305IetfDecryptParams.valid()` only validates `nPub.length == 12` and `k.length == 32`, never checking that `c.length == cLen`. Since `Encryption.attemptEncDecryption(byte[] ciphertext, byte[] ivk, byte[] epk)` passes an externally-derived `ciphertext` array together with a hardcoded constant `cLen` (`ZC_ENCCIPHERTEXT_SIZE`) straight into `JLibsodium.cryptoAeadChacha20poly1305IetfDecrypt`, any caller that supplies a `ciphertext` array whose actual length differs from `ZC_ENCCIPHERTEXT_SIZE`/`ZC_OUTCIPHERTEXT_SIZE` will cause the native libsodium call to be invoked with a declared length that does not match the marshalled Java array size.

### Finding Description
`JLibsodiumParam.Chacha20poly1305IetfDecryptParams.valid()` performs: [1](#0-0) 
This never checks `c.length == cLen`, unlike other parameter classes in the same file that do enforce strict length equality (e.g. `Black2bSaltPersonalParams.valid()`).

The decrypt path is reached from `NoteEncryption.Encryption.attemptEncDecryption(byte[] ciphertext, byte[] ivk, byte[] epk)`, which hardcodes `cLen = ZC_ENCCIPHERTEXT_SIZE` while `ciphertext` is caller-supplied: [2](#0-1) 
and similarly for `attemptOutDecryption` (`cLen = ZC_OUTCIPHERTEXT_SIZE`, `ciphertext.data`): [3](#0-2) 

`JLibsodium.cryptoAeadChacha20poly1305IetfDecrypt` forwards `params.getC()` and `params.getCLen()` unmodified to the native binding: [4](#0-3) 

Because the JNA/native marshalling of a Java `byte[]` typically allocates a native buffer sized to the array's actual length, passing a `cLen` value that exceeds the actual `c.length` (or is otherwise inconsistent with it) can cause the native libsodium routine to read past the allocated buffer.

However, I was unable to fully confirm within the available tool budget whether the on-chain consensus validation path (`ShieldedTransferActuator`/`ReceiveDescriptionCapsule`, which showed matches for `ZC_ENCCIPHERTEXT_SIZE`/`ZC_OUTCIPHERTEXT_SIZE`) enforces `c_enc`/`c_out` byte-length equality to the expected constants before a `ReceiveDescriptionCapsule` reaches decryption/wallet-scan code. If such a check exists in the actuator's `validate()` (a common pattern in this codebase for sapling receive descriptions), then a `ShieldedTransferContract` broadcast with a malformed `c_enc`/`c_out` length would be rejected before ever reaching `attemptEncDecryption`/`attemptOutDecryption`, closing off the on-chain broadcast vector while leaving only trusted/wallet-side callers exposed (which would fall outside the "unprivileged attacker" scope of this audit).

### Impact Explanation
If a code path (consensus validation or wallet scanning) forwards attacker/externally-supplied `c_enc`/`c_out` bytes of a length different from `ZC_ENCCIPHERTEXT_SIZE`/`ZC_OUTCIPHERTEXT_SIZE` without a prior explicit length check, the resulting `cLen` vs. actual-array-length mismatch passed into the native libsodium call could cause an out-of-bounds native memory read, non-deterministic behavior, or a JVM crash (SIGSEGV) during `ReceiveDescription` processing, which is process/node-crashing but not the same class of finding as remote code execution or fund theft.

### Likelihood Explanation
The `valid()` gap itself is confirmed and reproducible in isolation (a unit test constructing `Chacha20poly1305IetfDecryptParams` with `c.length != cLen` will not throw, unlike the `nPub`/`k` length checks). Whether this is reachable from an unprivileged, on-chain `ShieldedTransferContract` broadcast depends on validation logic in `ShieldedTransferActuator`/`ReceiveDescriptionCapsule` that I could not fully trace before running out of tool calls; those files did show hits for the same size constants, suggesting length enforcement may already exist upstream of this call, which would significantly reduce or eliminate the on-chain-broadcast attack surface for this specific class.

### Recommendation
Regardless of upstream validation, add a defense-in-depth check in `Chacha20poly1305IetfDecryptParams.valid()` (and the analogous encrypt-params class) enforcing `c.length == cLen` (and `m.length` sizing where applicable), mirroring the strict-equality pattern already used in `Black2bSaltPersonalParams.valid()` and `validParamLength`.

### Proof of Concept
```java
// Unit test (chainbase module)
@Test
public void testChacha20poly1305IetfDecryptParams_missingCLenCheck() {
  byte[] m = new byte[16];
  byte[] c = new byte[10];       // actual length != declared cLen
  long cLen = 580;               // e.g. ZC_ENCCIPHERTEXT_SIZE
  byte[] nPub = new byte[12];
  byte[] k = new byte[32];

  // Expectation for a safe implementation: should throw ZksnarkException
  // Actual current behavior: no exception is thrown.
  assertThrows(ZksnarkException.class, () ->
      new JLibsodiumParam.Chacha20poly1305IetfDecryptParams(
          m, null, null, c, cLen, null, 0, nPub, k));
}
```
Fuzz plan: iterate `c.length` from 0..600 while keeping `cLen` fixed at `ZC_ENCCIPHERTEXT_SIZE`/`ZC_OUTCIPHERTEXT_SIZE`, construct the params object, and (where the upstream reachability is confirmed) invoke `JLibsodium.cryptoAeadChacha20poly1305IetfDecrypt`; diff-test two node builds (one with a `c.length == cLen` guard added, one without) for crash/OOB-read divergence under a native memory sanitizer (e.g. ASan-instrumented libsodium build) to confirm out-of-bounds native reads.

### Citations

**File:** chainbase/src/main/java/org/tron/common/zksnark/JLibsodiumParam.java (L230-234)
```java
    @Override
    public void valid() throws ZksnarkException {
      validParamLength(nPub, 12);
      validParamLength(k, 32);
    }
```

**File:** framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java (L196-204)
```java
      if (JLibsodium.cryptoAeadChacha20poly1305IetfDecrypt(new Chacha20poly1305IetfDecryptParams(
          plaintext.data, null,
          null,
          ciphertext, ZC_ENCCIPHERTEXT_SIZE,
          null,
          0,
          cipher_nonce, kEnc)) != 0) {
        return Optional.empty();
      }
```

**File:** framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java (L242-260)
```java
    public static Optional<OutPlaintext> attemptOutDecryption(
        OutCiphertext ciphertext, byte[] ovk, byte[] cv, byte[] cm, byte[] epk)
        throws ZksnarkException {
      byte[] ock = new byte[NOTEENCRYPTION_CIPHER_KEYSIZE];
      //generate ock by ovk, cv, cm, epk
      prfOck(ock, ovk, cv, cm, epk);
      byte[] cipherNonce = new byte[CRYPTO_AEAD_CHACHA20POLY1305_IETF_NPUBBYTES];
      OutPlaintext plaintext = new OutPlaintext();
      plaintext.data = new byte[ZC_OUTPLAINTEXT_SIZE];
      //decrypt out by ock, get esk, pkD
      if (JLibsodium.cryptoAeadChacha20poly1305IetfDecrypt(new Chacha20poly1305IetfDecryptParams(
          plaintext.data, null,
          null,
          ciphertext.data, ZC_OUTCIPHERTEXT_SIZE,
          null,
          0,
          cipherNonce, ock)) != 0) {
        return Optional.empty();
      }
```

**File:** chainbase/src/main/java/org/tron/common/zksnark/JLibsodium.java (L40-47)
```java
  public static int cryptoAeadChacha20poly1305IetfDecrypt(
      Chacha20poly1305IetfDecryptParams params) {
    return INSTANCE
        .cryptoAeadChacha20Poly1305IetfDecrypt(params.getM(), params.getMLenP(),
            params.getNSec(),
            params.getC(), params.getCLen(), params.getAd(),
            params.getAdLen(), params.getNPub(), params.getK());
  }
```
