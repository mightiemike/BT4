### Title
Witness Private Key Passed as Command-Line Argument, Exposed via Shell History and Process Listing - ([File: framework/src/main/java/org/tron/core/config/args/CLIParameter.java])

### Summary
`CLIParameter` defines a `-p`/`--private-key` startup flag that accepts the witness's raw private key as a plaintext command-line argument when launching `FullNode`. [1](#0-0)  This value is consumed by `WitnessInitializer.initFromCLIPrivateKey`, which stores it directly into `LocalWitnesses` for use as the node's block-signing key. [2](#0-1) 

### Finding Description
Passing a witness's private key as a raw CLI argument (e.g. `java -jar FullNode.jar -p <hex_private_key> --witness`) matches the reported bug class: any argument supplied on a shell command line is persisted to the invoking shell's history file (`~/.bash_history`, `~/.zsh_history`, etc.) and is also visible to any other local user via `ps -ef`/`/proc/<pid>/cmdline` while the process runs. Unlike the newer `plugins` keystore tooling — `KeystoreImport`, which deliberately avoids CLI-argument key input and instead reads the key via `Console.readPassword` or a `--key-file`, explicitly rejecting non-interactive input without a file [3](#0-2)  — the `FullNode` witness startup path still accepts the raw signing key directly as a `-p` argument with no file-based or prompted alternative enforced. [4](#0-3) 

### Impact Explanation
The key exposed here is not an ordinary account key but the **witness (Super Representative) signing key**, used to produce and sign blocks in TRON's DPoS consensus. If this key leaks via shell history or process inspection (e.g., on a shared/administered server, backup of `.bash_history`, or a monitoring agent that captures process arguments), an attacker gains the ability to impersonate the witness — forging blocks, double-signing, or otherwise disrupting consensus integrity for that SR slot. This is a concrete consensus/authentication impact rather than a theoretical one, since witness keys are high-value, long-lived, and this flag is a documented, supported way to supply them at every node restart.

### Likelihood Explanation
Likelihood is moderate: exploitation requires local access to the operator's shell history or process table (e.g., a compromised or shared operations host, misconfigured logging/monitoring, or an insider on a hosting provider), which is a realistic operational condition for node operators who script deployments or restarts using `-p`. The codebase's own newer tooling (`KeystoreImport`) already treats this exact pattern as something to actively avoid, indicating the project recognizes the risk class but has not closed it for the witness startup path.

### Recommendation
1. Deprecate the `-p`/`--private-key` CLI flag for witness startup in favor of file-based or prompted input (mirroring `KeystoreImport`'s `--key-file`/interactive console pattern).
2. If backward compatibility must be preserved, emit a prominent deprecation warning at startup and document the bash-history/process-listing risk, similar to the `--keystore-factory` non-TTY truncation warning already present in `WitnessInitializer`. [5](#0-4) 
3. Prefer the existing `--keystore-factory` / keystore-file + password mechanisms (`initFromKeystore`) as the recommended witness key supply method. [6](#0-5) 

### Proof of Concept
```
java -jar FullNode.jar -w --private-key <64_hex_char_witness_private_key> --witness-address <address>
```
This key is captured verbatim in the invoking shell's history file (e.g. `~/.bash_history`) and is visible to any co-located user via `ps aux | grep FullNode` for the process lifetime, since `CLIParameter.privateKey` is populated straight from `argv` [1](#0-0)  and passed unmodified into `LocalWitnesses` for signing use. [7](#0-6)

### Citations

**File:** framework/src/main/java/org/tron/core/config/args/CLIParameter.java (L38-42)
```java
  @Parameter(names = {"-w", "--witness"}, description = "Is witness node")
  public boolean witness;

  @Parameter(names = {"-p", "--private-key"}, description = "Witness private key")
  public String privateKey;
```

**File:** framework/src/main/java/org/tron/core/config/args/WitnessInitializer.java (L21-43)
```java
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
```

**File:** framework/src/main/java/org/tron/core/config/args/WitnessInitializer.java (L60-113)
```java
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
```

**File:** plugins/src/main/java/common/org/tron/plugins/KeystoreImport.java (L122-152)
```java
  private String readPrivateKey(PrintWriter err) throws IOException {
    if (keyFile != null) {
      byte[] bytes = KeystoreCliUtils.readRegularFile(keyFile, 1024, "Key file", err);
      if (bytes == null) {
        return null;
      }
      try {
        return new String(bytes, StandardCharsets.UTF_8).trim();
      } finally {
        Arrays.fill(bytes, (byte) 0);
      }
    }

    Console console = System.console();
    if (console == null) {
      err.println("No interactive terminal available. "
          + "Use --key-file to provide private key.");
      return null;
    }

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
  }
```
