### Title
Uncontrolled PBKDF2/Scrypt iteration count in keystore decryption causes CPU exhaustion - (File: `crypto/src/main/java/org/tron/keystore/Wallet.java`)

### Summary
The external report describes CVE-2023-50658 in `jose2go`: an attacker can cause denial of service by supplying a very large PBES2 Count (`p2c`) value, forcing excessive PBKDF2 iterations. A directly analogous path exists in java-tron's keystore handling: `Wallet.decrypt` reads KDF parameters (`c` for PBKDF2, `n`/`p`/`r`/`dklen` for scrypt) from a user-supplied keystore JSON and passes them unchecked into `PKCS5S2ParametersGenerator` and `SCrypt.generate`. A malicious keystore with extreme iteration counts can consume CPU for an unbounded amount of time, constituting a denial-of-service condition for any code path that decrypts keystores on behalf of an unprivileged user.

### Finding Description
`Wallet.decrypt` in `crypto/src/main/java/org/tron/keystore/Wallet.java` deserializes a `WalletFile` and, based on the `kdf` field, either:
- calls `generateAes128CtrDerivedKey` with the user-controlled `c` value, which initializes `PKCS5S2ParametersGenerator` and runs PBKDF2-HMAC-SHA256 for `c` iterations; or
- calls `generateDerivedScryptKey` with user-controlled `n`, `r`, `p`, and `dklen`, which runs `SCrypt.generate`.

Neither path validates that the KDF parameters are within a reasonable bound. The `validationError` method only checks version, cipher, and KDF type strings, not numeric cost parameters. [1](#0-0) [2](#0-1) 

### Impact Explanation
A successful attack causes CPU exhaustion (denial of service) on the process performing decryption. This is a concrete halt/underpriced-public-work impact:
- **Wallet/API and node startup**: `WalletUtils.loadCredentials` is used by `KeystoreFactory` and `WitnessInitializer.initFromKeystore` to load SR/witness keystores at node startup. [3](#0-2) [4](#0-3) 
- **Toolkit CLI**: `KeystoreUpdate` and `KeystoreImport` read arbitrary keystore files and call `Wallet.decrypt`. [5](#0-4) 
- **Public/unprivileged input**: Keystore JSON files are intended to be portable and can be supplied by users importing wallets or by operators loading witness keystores. No authentication or rate-limiting is performed before the expensive KDF runs.

Because the MAC check happens after the KDF completes, an attacker does not need to know the password; a crafted keystore with a valid JSON structure but extreme KDF parameters is sufficient to force long-running computation.

### Likelihood Explanation
High. The attack requires only a crafted keystore file and any code path that calls `Wallet.decrypt` or `WalletUtils.loadCredentials` on it. The `WalletFile` POJOs accept arbitrary `int` values for `c`, `n`, `p`, `r`, and `dklen` via Jackson deserialization. [6](#0-5)  Existing tests demonstrate that PBKDF2 (`c=262144`) and scrypt (`n=262144`) keystores are accepted and decrypted normally, confirming the code path is reachable. [7](#0-6) 

### Recommendation
Enforce maximum KDF cost parameters in `Wallet.decrypt` before invoking the KDF:
- For PBKDF2 (`Aes128CtrKdfParams`), cap `c` to a safe maximum (e.g., 10,000,000) and reject negative or zero values.
- For scrypt (`ScryptKdfParams`), cap `n`, `p`, `r`, and `dklen` to safe maximums and validate `n` is a power of two greater than 1, as required by the scrypt specification.
- Reject any `dklen` that is unreasonably large to prevent memory blow-up in addition to CPU exhaustion.
- Consider performing the KDF in a bounded-time worker or adding a global timeout for keystore decryption, especially in API-facing paths.

### Proof of Concept
An attacker constructs a Web3 Secret Storage JSON with `kdf=pbkdf2` and an extremely large `c` value, for example:

```json
{
  "version": 3,
  "id": "00000000-0000-0000-0000-000000000000",
  "address": "TAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
  "crypto": {
    "cipher": "aes-128-ctr",
    "ciphertext": "00000000000000000000000000000000",
    "cipherparams": { "iv": "00000000000000000000000000000000" },
    "kdf": "pbkdf2",
    "kdfparams": {
      "c": 2147483647,
      "dklen": 32,
      "prf": "hmac-sha256",
      "salt": "0000000000000000000000000000000000000000000000000000000000000000"
    },
    "mac": "0000000000000000000000000000000000000000000000000000000000000000"
  }
}
```

When `Wallet.decrypt` is called with any password, it reaches:

```java
PKCS5S2ParametersGenerator gen = new PKCS5S2ParametersGenerator(new SHA256Digest());
gen.init(password, salt, c);
return ((KeyParameter) gen.generateDerivedParameters(256)).getKey();
``` [8](#0-7) 

With `c = Integer.MAX_VALUE`, PBKDF2 will perform ~2.1 billion HMAC-SHA256 iterations, consuming a CPU core for an extended period before the MAC mismatch is detected. The same effect can be achieved with scrypt by setting `n`, `p`, or `r` to very large values in `ScryptKdfParams`.

### Citations

**File:** crypto/src/main/java/org/tron/keystore/Wallet.java (L134-147)
```java
  private static byte[] generateAes128CtrDerivedKey(
      byte[] password, byte[] salt, int c, String prf) throws CipherException {

    if (!"hmac-sha256".equals(prf)) {
      throw new CipherException("Unsupported prf:" + prf);
    }

    // Java 8 supports this, but you have to convert the password to a character array, see
    // http://stackoverflow.com/a/27928435/3211687

    PKCS5S2ParametersGenerator gen = new PKCS5S2ParametersGenerator(new SHA256Digest());
    gen.init(password, salt, c);
    return ((KeyParameter) gen.generateDerivedParameters(256)).getKey();
  }
```

**File:** crypto/src/main/java/org/tron/keystore/Wallet.java (L175-208)
```java
  public static SignInterface decrypt(String password, WalletFile walletFile,
      boolean ecKey) throws CipherException {

    validate(walletFile);

    WalletFile.Crypto crypto = walletFile.getCrypto();

    byte[] mac = ByteArray.fromHexString(crypto.getMac());
    byte[] iv = ByteArray.fromHexString(crypto.getCipherparams().getIv());
    byte[] cipherText = ByteArray.fromHexString(crypto.getCiphertext());

    byte[] derivedKey;

    WalletFile.KdfParams kdfParams = crypto.getKdfparams();
    if (kdfParams instanceof WalletFile.ScryptKdfParams) {
      WalletFile.ScryptKdfParams scryptKdfParams =
          (WalletFile.ScryptKdfParams) crypto.getKdfparams();
      int dklen = scryptKdfParams.getDklen();
      int n = scryptKdfParams.getN();
      int p = scryptKdfParams.getP();
      int r = scryptKdfParams.getR();
      byte[] salt = ByteArray.fromHexString(scryptKdfParams.getSalt());
      derivedKey = generateDerivedScryptKey(password.getBytes(UTF_8), salt, n, r, p, dklen);
    } else if (kdfParams instanceof WalletFile.Aes128CtrKdfParams) {
      WalletFile.Aes128CtrKdfParams aes128CtrKdfParams =
          (WalletFile.Aes128CtrKdfParams) crypto.getKdfparams();
      int c = aes128CtrKdfParams.getC();
      String prf = aes128CtrKdfParams.getPrf();
      byte[] salt = ByteArray.fromHexString(aes128CtrKdfParams.getSalt());

      derivedKey = generateAes128CtrDerivedKey(password.getBytes(UTF_8), salt, c, prf);
    } else {
      throw new CipherException("Unable to deserialize params: " + crypto.getKdf());
    }
```

**File:** crypto/src/main/java/org/tron/keystore/Wallet.java (L246-263)
```java
  private static String validationError(WalletFile walletFile) {
    if (walletFile.getVersion() != CURRENT_VERSION) {
      return "Wallet version is not supported";
    }
    WalletFile.Crypto crypto = walletFile.getCrypto();
    if (crypto == null) {
      return "Missing crypto section";
    }
    String cipher = crypto.getCipher();
    if (cipher == null || !cipher.equals(CIPHER)) {
      return "Wallet cipher is not supported";
    }
    String kdf = crypto.getKdf();
    if (kdf == null || (!kdf.equals(PBKDF2) && !kdf.equals(SCRYPT))) {
      return "KDF type is not supported";
    }
    return null;
  }
```

**File:** crypto/src/main/java/org/tron/keystore/WalletUtils.java (L122-127)
```java
  public static Credentials loadCredentials(String password, File source, boolean ecKey)
      throws IOException, CipherException {
    warnIfSymbolicLink(source);
    WalletFile walletFile = objectMapper.readValue(source, WalletFile.class);
    return Credentials.create(Wallet.decrypt(password, walletFile, ecKey));
  }
```

**File:** framework/src/main/java/org/tron/core/config/args/WitnessInitializer.java (L1-120)
```java
package org.tron.core.config.args;

import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.tron.common.crypto.SignInterface;
import org.tron.common.utils.ByteArray;
import org.tron.common.utils.Commons;
import org.tron.common.utils.LocalWitnesses;
import org.tron.core.exception.CipherException;
import org.tron.core.exception.TronError;
import org.tron.keystore.Credentials;
import org.tron.keystore.WalletUtils;

@Slf4j
public class WitnessInitializer {

  /**
   * Init from a single private key (and optional witness address).
   */
  public static LocalWitnesses initFromCLIPrivateKey(
      String privateKey, String witnessAddress) {
    LocalWitnesses witnesses = new LocalWitnesses(privateKey);

    byte[] address = null;
    if (StringUtils.isNotEmpty(witnessAddress)) {
      address = Commons.decodeFromBase58Check(witnessAddress);
      if (address == null) {
        throw new TronError(
            "LocalWitnessAccountAddress format from cmd is incorrect",
            TronError.ErrCode.WITNESS_INIT);
      }
      logger.debug("Got localWitnessAccountAddress from cmd");
    }

    witnesses.initWitnessAccountAddress(
        address, Args.getInstance().isECKeyCryptoEngine());
    logger.debug("Got privateKey from cmd");
    return witnesses;
  }

  /**
   * Init from a list of private keys.
   */
  public static LocalWitnesses initFromCFGPrivateKey(
      List<String> privateKeys, String witnessAccountAddress) {
    LocalWitnesses witnesses = new LocalWitnesses();
    witnesses.setPrivateKeys(privateKeys);
    logger.debug("Got privateKey from config.conf");

    byte[] address = resolveWitnessAddress(witnesses, witnessAccountAddress);
    witnesses.initWitnessAccountAddress(
        address, Args.getInstance().isECKeyCryptoEngine());
    return witnesses;
  }

  /**
   * Init from keystore files with password.
   */
  public static LocalWitnesses initFromKeystore(
      List<String> keystoreFiles, String password,
      String witnessAccountAddress) {
    if (keystoreFiles.size() > 1) {
      logger.warn("Multiple keystores detected. Only the first keystore will be used"
          + " as witness, all others will be ignored.");
    }

    String fileName = System.getProperty("user.dir") + "/" + keystoreFiles.get(0);
    String pwd;
    if (StringUtils.isEmpty(password)) {
      System.out.println("Please input your password.");
      pwd = WalletUtils.inputPassword();
    } else {
      pwd = password;
    }

    List<String> privateKeys = new ArrayList<>();
    try {
      Credentials credentials = WalletUtils.loadCredentials(pwd, new File(fileName),
          Args.getInstance().isECKeyCryptoEngine());
      SignInterface sign = credentials.getSignInterface();
      String prikey = ByteArray.toHexString(sign.getPrivateKey());
      privateKeys.add(prikey);
    } catch (IOException | CipherException e) {
      logger.error("Witness node start failed!");
      // Legacy-truncation hint: if this keystore was created with
      // `FullNode.jar --keystore-factory` in non-TTY mode (e.g.
      // `echo PASS | java ...`), the legacy code encrypted with only
      // the first whitespace-separated word of the password. Emit the
      // tip only when the entered password has internal whitespace —
      // otherwise truncation cannot be the cause.
      if (e instanceof CipherException && pwd != null && pwd.matches(".*\\s.*")) {
        logger.error(
            "Tip: keystores created via `FullNode.jar --keystore-factory` in "
                + "non-TTY mode were encrypted with only the first "
                + "whitespace-separated word of the password. Try restarting "
                + "with only that first word as `-p`, then reset the password "
                + "via `java -jar Toolkit.jar keystore update`.");
      }
      throw new TronError(e, TronError.ErrCode.WITNESS_KEYSTORE_LOAD);
    }

    LocalWitnesses witnesses = new LocalWitnesses();
    witnesses.setPrivateKeys(privateKeys);
    byte[] address = resolveWitnessAddress(witnesses, witnessAccountAddress);
    witnesses.initWitnessAccountAddress(
        address, Args.getInstance().isECKeyCryptoEngine());
    logger.debug("Got privateKey from keystore");
    return witnesses;
  }

  static byte[] resolveWitnessAddress(
      LocalWitnesses witnesses, String witnessAccountAddress) {
    if (StringUtils.isEmpty(witnessAccountAddress)) {
      return null;
    }

```

**File:** plugins/src/main/java/common/org/tron/plugins/KeystoreUpdate.java (L140-178)
```java
      // Skip validation on old password: keystore may predate the minimum-length policy
      if (!WalletUtils.passwordValid(newPassword)) {
        err.println("Invalid new password: must be at least 6 characters.");
        return 1;
      }

      boolean ecKey = !sm2;
      // Re-read via NOFOLLOW byte channel to close the TOCTOU window between
      // findKeystoreByAddress and this read — an attacker with directory
      // write access could otherwise swap the file for a symlink in between.
      byte[] keystoreBytes = KeystoreCliUtils.readKeystoreFile(keystoreFile, err);
      if (keystoreBytes == null) {
        // readKeystoreFile already printed the specific reason
        return 1;
      }
      WalletFile walletFile = MAPPER.readValue(keystoreBytes, WalletFile.class);
      SignInterface keyPair = Wallet.decrypt(oldPassword, walletFile, ecKey);

      // createStandard already sets the correctly-derived address. Do NOT override
      // with walletFile.getAddress() — that would propagate a potentially spoofed
      // address from the JSON.
      WalletFile newWalletFile = Wallet.createStandard(newPassword, keyPair);
      // writeWalletFile does a secure temp-file + atomic rename internally.
      WalletUtils.writeWalletFile(newWalletFile, keystoreFile);

      // Use the derived address from newWalletFile, not walletFile.getAddress().
      // Defense-in-depth: Wallet.decrypt already rejects spoofed addresses, but
      // relying on the derived value keeps this code correct even if that check
      // is ever weakened.
      String verifiedAddress = newWalletFile.getAddress();
      if (json) {
        KeystoreCliUtils.printJson(out, err, KeystoreCliUtils.jsonMap(
            "address", verifiedAddress,
            "file", keystoreFile.getName(),
            "status", "updated"));
      } else {
        out.println("Password updated for: " + verifiedAddress);
      }
      return 0;
```

**File:** crypto/src/main/java/org/tron/keystore/WalletFile.java (L284-414)
```java
  public static class Aes128CtrKdfParams implements KdfParams {

    private int dklen;
    private int c;
    private String prf;
    private String salt;

    public Aes128CtrKdfParams() {
    }

    public int getDklen() {
      return dklen;
    }

    public void setDklen(int dklen) {
      this.dklen = dklen;
    }

    public int getC() {
      return c;
    }

    public void setC(int c) {
      this.c = c;
    }

    public String getPrf() {
      return prf;
    }

    public void setPrf(String prf) {
      this.prf = prf;
    }

    public String getSalt() {
      return salt;
    }

    public void setSalt(String salt) {
      this.salt = salt;
    }

    @Override
    public boolean equals(Object o) {
      if (this == o) {
        return true;
      }
      if (o == null) {
        return false;
      }
      if (o.getClass() != this.getClass()) {
        return false;
      }

      Aes128CtrKdfParams that = (Aes128CtrKdfParams) o;

      if (dklen != that.dklen) {
        return false;
      }
      if (c != that.c) {
        return false;
      }
      if (getPrf() != null
          ? !getPrf().equals(that.getPrf())
          : that.getPrf() != null) {
        return false;
      }
      return getSalt() != null
          ? getSalt().equals(that.getSalt()) : that.getSalt() == null;
    }

    @Override
    public int hashCode() {
      int result = dklen;
      result = 31 * result + c;
      result = 31 * result + (getPrf() != null ? getPrf().hashCode() : 0);
      result = 31 * result + (getSalt() != null ? getSalt().hashCode() : 0);
      return result;
    }
  }

  public static class ScryptKdfParams implements KdfParams {

    private int dklen;
    private int n;
    private int p;
    private int r;
    private String salt;

    public ScryptKdfParams() {
    }

    public int getDklen() {
      return dklen;
    }

    public void setDklen(int dklen) {
      this.dklen = dklen;
    }

    public int getN() {
      return n;
    }

    public void setN(int n) {
      this.n = n;
    }

    public int getP() {
      return p;
    }

    public void setP(int p) {
      this.p = p;
    }

    public int getR() {
      return r;
    }

    public void setR(int r) {
      this.r = r;
    }

    public String getSalt() {
      return salt;
    }

    public void setSalt(String salt) {
      this.salt = salt;
    }
```

**File:** framework/src/test/java/org/tron/keystore/CrossImplTest.java (L41-68)
```java
  private static final String ETH_PBKDF2_KEYSTORE = "{"
      + "\"crypto\":{\"cipher\":\"aes-128-ctr\","
      + "\"cipherparams\":{\"iv\":\"02ebc768684e5576900376114625ee6f\"},"
      + "\"ciphertext\":\"7ad5c9dd2c95f34a92ebb86740b92103a5d1cc4c2eabf3b9a59e1f83f3181216\","
      + "\"kdf\":\"pbkdf2\","
      + "\"kdfparams\":{\"c\":262144,\"dklen\":32,\"prf\":\"hmac-sha256\","
      + "\"salt\":\"0e4cf3893b25bb81efaae565728b5b7cde6a84e224cbf9aed3d69a31c981b702\"},"
      + "\"mac\":\"2b29e4641ec17f4dc8b86fc8592090b50109b372529c30b001d4d96249edaf62\"},"
      + "\"id\":\"af0451b4-6020-4ef0-91ec-794a5a965b01\",\"version\":3}";

  private static final String ETH_SCRYPT_KEYSTORE = "{"
      + "\"crypto\":{\"cipher\":\"aes-128-ctr\","
      + "\"cipherparams\":{\"iv\":\"3021e1ef4774dfc5b08307f3a4c8df00\"},"
      + "\"ciphertext\":\"4dd29ba18478b98cf07a8a44167acdf7e04de59777c4b9c139e3d3fa5cb0b931\","
      + "\"kdf\":\"scrypt\","
      + "\"kdfparams\":{\"dklen\":32,\"n\":262144,\"r\":8,\"p\":1,"
      + "\"salt\":\"4f9f68c71989eb3887cd947c80b9555fce528f210199d35c35279beb8c2da5ca\"},"
      + "\"mac\":\"7e8f2192767af9be18e7a373c1986d9190fcaa43ad689bbb01a62dbde159338d\"},"
      + "\"id\":\"7654525c-17e0-4df5-94b5-c7fde752c9d2\",\"version\":3}";

  @Test
  public void testDecryptEthPbkdf2Keystore() throws Exception {
    WalletFile walletFile = MAPPER.readValue(ETH_PBKDF2_KEYSTORE, WalletFile.class);
    SignInterface recovered = Wallet.decrypt(ETH_PASSWORD, walletFile, true);
    assertEquals("Private key must match Ethereum test vector",
        ETH_PRIVATE_KEY,
        org.tron.common.utils.ByteArray.toHexString(recovered.getPrivateKey()));
  }
```
