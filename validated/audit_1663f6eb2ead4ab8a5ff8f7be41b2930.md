### Title
Missing length validation in `NoteEncryption.encryptToRecipient` before native `librustzcashKaAgree`/`libsodium` calls - (File: `framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java`)

### Summary
`NoteEncryption.encryptToRecipient` forwards the caller-supplied `pkD` byte array directly into `JLibrustzcash.librustzcashKaAgree` (which calls the native Rust `librustzcashSaplingKaAgree` via JNI) without any prior length check, unlike several sibling methods in `JLibrustzcash` that explicitly validate fixed lengths (e.g. `librustzcashToScalar`, `librustzcashSaplingGenerateR`, `librustzcashCheckDiversifier`) using `LibrustzcashParam.valid32Params`/`validParamLength`. `EncPlaintext.data` is likewise unguarded by a setter-time size check.

### Finding Description
Inside `encryptToRecipient` [1](#0-0) , `pkD` is passed straight into `new KaAgreeParams(pkD, esk, dhsecret)` and then into `JLibrustzcash.librustzcashKaAgree`, which calls the native binding `INSTANCE.librustzcashSaplingKaAgree(params.getP(), params.getSk(), params.getResult())` [2](#0-1) . Compare this to other native entry points in the same class that validate fixed byte-array lengths before calling into native code, e.g. `librustzcashAskToAk`, `librustzcashNskToNk`, `librustzcashSaplingGenerateR`, `librustzcashCheckDiversifier`, and `librustzcashToScalar` [3](#0-2) [4](#0-3) [5](#0-4) . `librustzcashKaAgree` has no equivalent guard. Similarly, the ChaCha20-Poly1305 call in `encryptToRecipient` passes `message.data` with a hard-coded `ZC_ENCPLAINTEXT_SIZE` length constant to `JLibsodium.cryptoAeadChacha20Poly1305IetfEncrypt`, but `EncPlaintext.data` is a mutable, publicly settable field (`@Setter`) [6](#0-5) , so nothing in `encryptToRecipient` itself re-checks that `message.data.length == ZC_ENCPLAINTEXT_SIZE` at call time.

I could not locate, within the indexed portion of this repository, the actual call site(s) that construct the `pkD`/`EncPlaintext` values passed into `encryptToRecipient` from an externally reachable RPC/transaction path (the earlier grep hit in `Note.java` could not be re-confirmed on a second, more targeted search, and I ran out of iterations before resolving this discrepancy). This matters because `encryptToRecipient` is invoked during *construction* of a shielded output — i.e., typically on the sender/wallet side when building a shielded transaction locally (via a wallet/RPC "create shielded transaction" style call) — rather than during on-chain validation of an already-broadcast transaction. If that is the case, a malformed input would primarily affect the calling RPC session's own thread rather than being trivially triggerable by a third party's broadcast transaction against arbitrary other nodes. I was not able to fully confirm this distinction from the available index.

### Impact Explanation
If the native `librustzcashSaplingKaAgree` implementation (Rust, not present in this Java-indexed repo) trusts array length implicitly (e.g., always reads/writes a fixed 32-byte window via raw pointer arithmetic once obtained through JNI `GetByteArrayElements`), then supplying a `pkD` array shorter than 32 bytes could cause an out-of-bounds read/write in the native library, potentially crashing the JVM process (denial of service) or, in the worst case, corrupting adjacent native memory. This would map to TRON's "Node crash / DoS" impact class if reachable without privilege. However, without visibility into the native Rust binding's actual bounds behavior and without a confirmed remotely-reachable call path that feeds attacker-controlled variable-length byte arrays into `encryptToRecipient` from an unprivileged, non-local context, I cannot confirm this rises to a fully validated, unprivileged, remotely-triggerable "Node RCE/crash" per the strict validation rules of this audit.

### Likelihood Explanation
Low-to-uncertain. The absence of explicit length checks in `encryptToRecipient`/`librustzcashKaAgree` (relative to sibling methods that do validate) is a genuine code-quality/defense-in-depth gap. But exploitability depends on (a) the native Rust/libsodium binding's actual behavior on mismatched buffer sizes, which is outside the scope of the indexed Java code, and (b) confirming that an unprivileged, remote actor (not merely the local caller of their own wallet-side note-construction call) can reach this function with arbitrary-length byte arrays — which I was unable to conclusively establish from the available context.

### Recommendation
Add explicit pre-call length validation in `NoteEncryption.encryptToRecipient` (and its sibling `encryptToOurselves`) mirroring the pattern already used in `LibrustzcashParam.valid32Params`/`validParamLength`: validate `pkD.length == 32`, `esk.length == 32`, `epk.length == 32`, and `message.data.length == ZC_ENCPLAINTEXT_SIZE` before invoking `JLibrustzcash.librustzcashKaAgree` and `JLibsodium.cryptoAeadChacha20Poly1305IetfEncrypt`, throwing `ZksnarkException` on mismatch, consistent with how `librustzcashToScalar` and other methods already guard native calls in `JLibrustzcash.java`.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/zen/note/NoteEncryptionBoundsTest.java
@Test
public void testEncryptToRecipientRejectsMalformedPkD() throws Exception {
  NoteEncryption ne = NoteEncryption.fromDiversifier(new DiversifierT(new byte[11])).get();
  byte[] badPkD = new byte[4]; // malformed: not 32 bytes
  NoteEncryption.Encryption.EncPlaintext message = new NoteEncryption.Encryption.EncPlaintext();
  // EXPECTED: ZksnarkException thrown before reaching JLibrustzcash.librustzcashKaAgree
  // ACTUAL (current code): no length check exists in encryptToRecipient; badPkD is passed
  // directly into KaAgreeParams and into the native call.
  ne.encryptToRecipient(badPkD, message);
}
```
Note: this PoC demonstrates the absence of a Java-level pre-call length guard; it does not by itself prove native memory corruption, since that depends on the native Rust binding's internal behavior, which is outside this repository's indexed Java sources.

### Citations

**File:** framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java (L70-91)
```java
  public Optional<EncCiphertext> encryptToRecipient(byte[] pkD, EncPlaintext message)
      throws ZksnarkException {
    if (alreadyEncryptedEnc) {
      throw new ZksnarkException("already encrypted to the recipient using this key");
    }

    byte[] dhsecret = new byte[32];
    if (!JLibrustzcash.librustzcashKaAgree(new KaAgreeParams(pkD, esk, dhsecret))) {
      return Optional.empty();
    }

    byte[] kEnc = new byte[NOTEENCRYPTION_CIPHER_KEYSIZE];
    //generate kEnc by sharedsecret and epk
    Encryption.kdfSapling(kEnc, dhsecret, epk);
    byte[] cipherNonce = new byte[CRYPTO_AEAD_CHACHA20POLY1305_IETF_NPUBBYTES];
    EncCiphertext ciphertext = new EncCiphertext();
    JLibsodium.cryptoAeadChacha20Poly1305IetfEncrypt(new Chacha20Poly1305IetfEncryptParams(
        ciphertext.data, null, message.data,
        ZenChainParams.ZC_ENCPLAINTEXT_SIZE, null, 0, null, cipherNonce, kEnc));
    alreadyEncryptedEnc = true;
    return Optional.of(ciphertext);
  }
```

**File:** framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java (L403-408)
```java
    public static class EncPlaintext {

      @Getter
      @Setter
      private byte[] data = new byte[ZC_ENCPLAINTEXT_SIZE]; // ZC_ENCPLAINTEXT_SIZE
    }
```

**File:** chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java (L56-58)
```java
  public static boolean librustzcashKaAgree(KaAgreeParams params) {
    return INSTANCE.librustzcashSaplingKaAgree(params.getP(), params.getSk(), params.getResult());
  }
```

**File:** chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java (L75-102)
```java
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

  // void librustzcash_nsk_to_nk(const unsigned char *nsk, unsigned char *result);

  /**
   * @return r: random number, less than r_J,   32 bytes
   */
  public static byte[] librustzcashSaplingGenerateR(byte[] r) throws ZksnarkException {
    LibrustzcashParam.valid32Params(r);
    INSTANCE.librustzcashSaplingGenerateR(r);
    return r;
  }
```

**File:** chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java (L118-121)
```java
  public static boolean librustzcashCheckDiversifier(byte[] d) throws ZksnarkException {
    LibrustzcashParam.valid11Params(d);
    return INSTANCE.librustzcashCheckDiversifier(d);
  }
```

**File:** chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java (L152-156)
```java
  public static void librustzcashToScalar(byte[] value, byte[] data) throws ZksnarkException {
    LibrustzcashParam.validParamLength(value, 64);
    LibrustzcashParam.valid32Params(data);
    INSTANCE.librustzcashToScalar(value, data);
  }
```
