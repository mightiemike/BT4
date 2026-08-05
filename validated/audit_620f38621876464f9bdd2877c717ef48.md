Based on the codebase analysis, I found a concrete analog to the "unencrypted secret material lingering in process memory" bug class in java-tron's keystore/wallet tooling.

### Title
Private Key and Password Retained as Immutable, Unclearable Java `String` Objects in Wallet/Keystore Tooling - (File: `plugins/src/main/java/common/org/tron/plugins/KeystoreImport.java`)

### Summary
Across java-tron's keystore/wallet CLI tooling (`Toolkit.jar keystore import/update/new`, the legacy `FullNode.jar --keystore-factory`, and `WitnessInitializer`), the raw private key and password are read into `char[]` buffers that are correctly zeroed with `Arrays.fill(...)` after use, but they are then converted into `String` objects (`new String(key)` / `new String(pwd1)`) for the rest of the processing pipeline. Because `java.lang.String` is immutable and cannot be forcibly zeroed, the plaintext private key and password remain resident in the JVM heap for an indeterminate time (until GC), and can be recovered via heap/core dumps, swap files, or a compromised-host memory scrape — the same underlying bug class as the reported "unencrypted mnemonic phrase in-memory" issue, just applied to raw private keys/passwords instead of a BIP-39 mnemonic.

### Finding Description
The `readPrivateKey` helper in `KeystoreImport` reads the private key via a masked console prompt into a `char[]`, explicitly zeroes it in a `finally` block, but returns `new String(key)`, which is a fresh, immutable, un-zeroable copy of the secret: [1](#0-0) 

That `String privateKey` is then carried through the rest of `call()` — converted via `ByteArray.fromHexString(privateKey)` — and never cleared: [2](#0-1) 

The same pattern applies to passwords in `KeystoreCliUtils.readPassword`, where `char[]` buffers are zeroed but the returned `String password` is not: [3](#0-2) 

and in `KeystoreUpdate`, where both old/new passwords end up as `String` objects that persist for the remainder of the process: [4](#0-3) 

The legacy, still-shipped `KeystoreFactory` tool is worse — it reads the private key directly as a `String` via `Scanner.nextLine()` with no `char[]`/zeroing step at all: [5](#0-4) 

Finally, the decryption path itself takes the password as a `String` and calls `password.getBytes(UTF_8)`, producing yet another un-zeroed copy that is fed into the KDF: [6](#0-5) 
and `WalletUtils.loadCredentials(String password, ...)` propagates this `String`-based password through the whole call chain: [7](#0-6) 

Additionally, `WitnessInitializer`/`LocalWitnesses` hold the plaintext hex private key as a `String` in a `List<String>` for the entire runtime of a witness (SR) node, re-derived from it on every signing/mining check with no clearing mechanism: [8](#0-7) 

### Impact Explanation
Any operator running `Toolkit.jar keystore import/update/new`, the deprecated `--keystore-factory`, or an SR node started with `--private-key`/config-based private keys exposes the full plaintext private key and/or password as un-zeroable `String` objects in the JVM heap. An attacker who gains read access to the host (malware, another process on a shared/virtualized host, a crash/core dump, a swap file, or a heap dump taken for "debugging") can extract these secrets long after the CLI command completed or during the SR node's entire uptime — directly compromising the wallet/account controlled by that key, exactly analogous to the reported mnemonic-in-memory exposure (full account takeover / fund theft), just via the raw private key rather than a mnemonic.

### Likelihood Explanation
Exploitation requires local/host-level access (the same precondition as the original report), but this is a realistic threat model for any keystore-import/update workflow or long-running witness process — enterprise SR operators, cloud VMs, and shared CI/build hosts are common deployment targets, and core/heap dumps are frequently generated automatically on crash or via monitoring agents, making this a practically reachable analog rather than a purely theoretical one.

### Recommendation
Avoid converting sensitive `char[]` buffers to `String` at all in these code paths. Use `char[]`/`byte[]` end-to-end (e.g., a `CharBuffer`-based KDF input, or overloads of `SCrypt.generate`/`PKCS5S2ParametersGenerator` operating on byte arrays derived without an intermediate `String`), and explicitly zero those buffers (`Arrays.fill`) in `finally` blocks immediately after use in `KeystoreImport`, `KeystoreUpdate`, `KeystoreCliUtils`, `Wallet.decrypt`, `WalletUtils`, and `LocalWitnesses`/`WitnessInitializer`. Where a `String` is unavoidable (e.g., due to existing public API signatures), document the residual risk and consider migrating those APIs to accept `char[]`/`byte[]`.

### Proof of Concept
1. Run `java -jar Toolkit.jar keystore import --key-file key.txt --password-file pass.txt`.
2. During or shortly after import, trigger a JVM heap dump of the running `Toolkit.jar` process (`jmap -dump:live,format=b,file=heap.bin <pid>`) or simulate host compromise via a memory scraper.
3. Search the heap dump for the plaintext private key/password strings (e.g., via `strings heap.bin | grep <known-hex-prefix>`); they are recoverable because `readPrivateKey`/`readPassword` produced `String` copies of the secrets that were never zeroed, unlike the `char[]` inputs which were explicitly cleared.
4. For the SR-node case, start a witness node with `--private-key <hex>` and take a heap/core dump at any point during normal operation; the plaintext key is recoverable from `LocalWitnesses.privateKeys` for the entire process lifetime.

### Citations

**File:** plugins/src/main/java/common/org/tron/plugins/KeystoreImport.java (L66-93)
```java
      String privateKey = readPrivateKey(err);
      if (privateKey == null) {
        return 1;
      }

      if (privateKey.startsWith("0x") || privateKey.startsWith("0X")) {
        privateKey = privateKey.substring(2);
      }
      if (!isValidPrivateKey(privateKey)) {
        err.println("Invalid private key: must be 64 hex characters.");
        return 1;
      }

      String password = KeystoreCliUtils.readPassword(passwordFile, err);
      if (password == null) {
        return 1;
      }

      boolean ecKey = !sm2;
      SignInterface keyPair;
      try {
        keyPair = SignUtils.fromPrivate(
            ByteArray.fromHexString(privateKey), ecKey);
      } catch (Exception e) {
        err.println("Invalid private key: not a valid key"
            + " for the selected algorithm.");
        return 1;
      }
```

**File:** plugins/src/main/java/common/org/tron/plugins/KeystoreImport.java (L142-151)
```java
    char[] key = console.readPassword("Enter private key (hex): ");
    if (key == null) {
      err.println("Input cancelled.");
      return null;
    }
    try {
      return new String(key);
    } finally {
      Arrays.fill(key, '\0');
    }
```

**File:** plugins/src/main/java/common/org/tron/plugins/KeystoreCliUtils.java (L143-168)
```java
    char[] pwd1 = console.readPassword("Enter password: ");
    if (pwd1 == null) {
      err.println("Password input cancelled.");
      return null;
    }
    char[] pwd2 = console.readPassword("Confirm password: ");
    if (pwd2 == null) {
      Arrays.fill(pwd1, '\0');
      err.println("Password input cancelled.");
      return null;
    }
    try {
      if (!Arrays.equals(pwd1, pwd2)) {
        err.println("Passwords do not match.");
        return null;
      }
      String password = new String(pwd1);
      if (!WalletUtils.passwordValid(password)) {
        err.println("Invalid password: must be at least 6 characters.");
        return null;
      }
      return password;
    } finally {
      Arrays.fill(pwd1, '\0');
      Arrays.fill(pwd2, '\0');
    }
```

**File:** plugins/src/main/java/common/org/tron/plugins/KeystoreUpdate.java (L125-137)
```java
        try {
          oldPassword = new String(oldPwd);
          newPassword = new String(newPwd);
          String confirmPassword = new String(confirmPwd);
          if (!newPassword.equals(confirmPassword)) {
            err.println("New passwords do not match.");
            return 1;
          }
        } finally {
          Arrays.fill(oldPwd, '\0');
          Arrays.fill(newPwd, '\0');
          Arrays.fill(confirmPwd, '\0');
        }
```

**File:** framework/src/main/java/org/tron/program/KeystoreFactory.java (L83-94)
```java
  private void importPrivateKey() throws CipherException, IOException {
    Scanner in = new Scanner(System.in);
    String privateKey;
    System.out.println("Please input private key.");
    while (true) {
      String input = in.nextLine().trim();
      privateKey = input.split("\\s+")[0];
      if (priKeyValid(privateKey)) {
        break;
      }
      System.out.println("Invalid private key, please input again.");
    }
```

**File:** crypto/src/main/java/org/tron/keystore/Wallet.java (L175-205)
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

**File:** chainbase/src/main/java/org/tron/common/utils/LocalWitnesses.java (L65-105)
```java
  public void setPrivateKeys(final List<String> privateKeys) {
    if (CollectionUtils.isEmpty(privateKeys)) {
      return;
    }
    for (String privateKey : privateKeys) {
      validate(privateKey);
    }
    this.privateKeys = privateKeys;
  }

  private void validate(String privateKey) {
    if (StringUtils.startsWithIgnoreCase(privateKey, "0X")) {
      privateKey = privateKey.substring(2);
    }

    if (StringUtils.isBlank(privateKey)
        || privateKey.length() != ChainConstant.PRIVATE_KEY_LENGTH) {
      throw new TronError(String.format("private key must be %d hex string, actual: %d",
          ChainConstant.PRIVATE_KEY_LENGTH,
          StringUtils.isBlank(privateKey) ? 0 : privateKey.length()),
          TronError.ErrCode.WITNESS_INIT);
    }
    if (!StringUtil.isHexadecimal(privateKey)) {
      throw new TronError("private key must be hex string",
          TronError.ErrCode.WITNESS_INIT);
    }
  }

  public void addPrivateKeys(String privateKey) {
    validate(privateKey);
    this.privateKeys.add(privateKey);
  }

  //get the first one recently
  public String getPrivateKey() {
    if (CollectionUtils.isEmpty(privateKeys)) {
      logger.warn("PrivateKey is null.");
      return null;
    }
    return privateKeys.get(0);
  }
```
