Confirmed: there is no permission check anywhere in the config-loading path for `config.conf`, and `initFromCFGPrivateKey` at `framework/src/main/java/org/tron/core/config/args/WitnessInitializer.java:48-58` loads private keys straight from the HOCON list without any file-permission validation or warning, unlike the hardened `Toolkit.jar keystore` commands which enforce 0600 permissions.

### Title
Plaintext Super Representative (witness) private keys stored unprotected in `config.conf` with no permission check or warning - (File: `framework/src/main/java/org/tron/core/config/args/WitnessInitializer.java`)

### Summary
Java-tron's witness/Super Representative (SR) node supports a `localwitness` array in `config.conf` that stores the raw block-signing private key in plaintext, mirroring the price-feeder's plaintext keyring-password storage bug class. There is no check of the configuration file's filesystem permissions and no warning is emitted if the file is world- or group-readable, even though a hardened alternative (`localwitnesskeystore` via the encrypted `Toolkit.jar keystore` files, which enforce owner-only 0600 permissions) already exists in the same codebase.

### Finding Description
`config.conf`/`reference.conf` document and default-ship a `localwitness` array intended to hold the witness's raw private key in hex, e.g. `localwitness = [ "your-private-key-hex" ]` [1](#0-0) , and the same pattern appears in the shipped resource file [2](#0-1) . This list is parsed as plain strings by `LocalWitnessConfig.fromConfig` with no encryption, no permission check, and no warning [3](#0-2) . `Args.initLocalWitnesses` then dispatches directly to `WitnessInitializer.initFromCFGPrivateKey` whenever `localwitness` is non-empty, without any inspection of the config file's POSIX permissions or ownership [4](#0-3) , and `initFromCFGPrivateKey` simply assigns the plaintext keys to the in-memory witness object [5](#0-4) . No code path in the config-loading pipeline calls `Files.getPosixFilePermissions` or any equivalent check for `config.conf` (unlike the keystore file writer, which does enforce `OWNER_READ|OWNER_WRITE` on generated keystore JSON via `WalletUtils.writeWalletFile`) [6](#0-5) . This is a direct structural analog to the reported price-feeder bug: a sensitive secret (there, a keyring password; here, the SR block-signing private key itself) is stored unencrypted in a plaintext configuration file with no permission validation, no stdin-based safer alternative promoted for the primary flow, and only a passing comment ("use localwitnesskeystore for production") rather than an enforced or warned-about safeguard.

### Impact Explanation
An attacker who obtains read access to a Super Representative's `config.conf` — via backup exposure, misconfigured permissions, a compromised low-privilege local account, or an accidental repo/artifact leak — obtains the raw, unencrypted private key controlling that SR's block-production identity. This key can be used to forge blocks, sign malicious votes/proposals, or otherwise hijack the witness's on-chain identity, which is a direct authentication/authority compromise of a privileged network role (consensus-critical, unlike the price-feeder's oracle-only role). Because SR keys are among the highest-value secrets in the java-tron trust model, exposure has more severe consequences than the original price-feeder finding.

### Likelihood Explanation
The primary documented example in `docs/configuration.md` shows the plaintext `localwitness` form first, with the safer `localwitnesskeystore` form commented out and only recommended in a code comment; operators following the documentation's first example, or using the shipped `config.conf`/`reference.conf` templates as a base, are likely to store keys in plaintext by default. No runtime warning fires even when the file has overly broad permissions (e.g., world-readable), so misconfiguration is silent, exactly matching the original report's exploit scenario of an attacker gaining local access and reading the file.

### Recommendation
- On `localwitness` config load, check `config.conf`'s POSIX permissions (mirroring the existing `OWNER_READ|OWNER_WRITE`-only pattern already implemented for keystore files in `WalletUtils.writeWalletFile`) and log a prominent warning (or refuse to start) if the file is group- or world-readable.
- Prefer/require the encrypted `localwitnesskeystore` path for production deployments; consider deprecating or gating the plaintext `localwitness` array behind an explicit opt-in flag.
- Update `docs/configuration.md` and `reference.conf` to lead with the keystore-based example and add explicit documentation of the risk of plaintext private keys in `config.conf`, plus guidance on securing backups of this file.

### Proof of Concept
1. Deploy a witness node using the default `config.conf` template with `localwitness = ["<raw-private-key-hex>"]` as shown in the shipped resource file [7](#0-6) .
2. Leave the file at default/broad permissions (e.g., `644` or inherited from an insecure deployment script/backup).
3. An attacker with local (even unprivileged, e.g. another OS user) or backup access reads `config.conf` and extracts the plaintext key from the `localwitness` array.
4. The node itself never checks or warns about this — `Args.initLocalWitnesses` → `WitnessInitializer.initFromCFGPrivateKey` loads the key with no permission validation [8](#0-7) [5](#0-4) .
5. The attacker now controls the SR's block-signing key and can impersonate the witness on the network.

### Citations

**File:** docs/configuration.md (L149-153)
```markdown
```hocon
# Plain private key (use localwitnesskeystore for production)
localwitness = [
  "your-private-key-hex"
]
```

**File:** framework/src/main/resources/config.conf (L404-411)
```text
# Optional. Used when the witness account has set witnessPermission.
# localWitnessAccountAddress is the witness account address;
# localwitness is configured with the private key of the witnessPermissionAddress.
# When empty, localwitness is the private key of the witness account itself.
# localWitnessAccountAddress =

localwitness = [
]
```

**File:** common/src/main/java/org/tron/core/config/args/LocalWitnessConfig.java (L22-34)
```java
  public static LocalWitnessConfig fromConfig(Config config) {
    LocalWitnessConfig lw = new LocalWitnessConfig();
    if (config.hasPath("localwitness")) {
      lw.privateKeys = config.getStringList("localwitness");
    }
    if (config.hasPath("localWitnessAccountAddress")) {
      lw.accountAddress = config.getString("localWitnessAccountAddress");
    }
    if (config.hasPath("localwitnesskeystore")) {
      lw.keystores = config.getStringList("localwitnesskeystore");
    }
    return lw;
  }
```

**File:** framework/src/main/java/org/tron/core/config/args/Args.java (L913-920)
```java
    LocalWitnessConfig lwConfig = LocalWitnessConfig.fromConfig(config);

    // path 2: config localwitness (private key list)
    if (!lwConfig.getPrivateKeys().isEmpty()) {
      localWitnesses = WitnessInitializer.initFromCFGPrivateKey(
          lwConfig.getPrivateKeys(), lwConfig.getAccountAddress());
      return;
    }
```

**File:** framework/src/main/java/org/tron/core/config/args/WitnessInitializer.java (L48-58)
```java
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
```

**File:** crypto/src/main/java/org/tron/keystore/WalletUtils.java (L64-101)
```java
  /**
   * Write a WalletFile to the given destination path with owner-only (0600)
   * permissions, using a temp file + atomic rename.
   *
   * <p>On POSIX filesystems, the temp file is created atomically with 0600
   * permissions via {@link Files#createTempFile(Path, String, String,
   * java.nio.file.attribute.FileAttribute[])}, avoiding any window where the
   * file is world-readable.
   *
   * <p>On non-POSIX filesystems (e.g. Windows) the fallback uses
   * {@link File#setReadable(boolean, boolean)} /
   * {@link File#setWritable(boolean, boolean)} which is best-effort — these
   * methods manipulate only DOS-style attributes on Windows and may not update
   * file ACLs. The sensitive keystore JSON is written only after the narrowing
   * calls, so no confidential data is exposed during the window, but callers
   * on Windows should not infer strict owner-only ACL enforcement from this.
   *
   * @param walletFile  the keystore to serialize
   * @param destination the final target file (existing file will be replaced)
   */
  public static void writeWalletFile(WalletFile walletFile, File destination)
      throws IOException {
    Path dir = destination.getAbsoluteFile().getParentFile().toPath();
    Files.createDirectories(dir);

    Path tmp;
    try {
      tmp = Files.createTempFile(dir, "keystore-", ".tmp",
          PosixFilePermissions.asFileAttribute(OWNER_ONLY));
    } catch (UnsupportedOperationException e) {
      // Windows / non-POSIX fallback — best-effort narrowing only (see JavaDoc)
      tmp = Files.createTempFile(dir, "keystore-", ".tmp");
      File tf = tmp.toFile();
      tf.setReadable(false, false);
      tf.setReadable(true, true);
      tf.setWritable(false, false);
      tf.setWritable(true, true);
    }
```
