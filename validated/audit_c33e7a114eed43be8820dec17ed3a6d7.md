### Title
Witness private key exposed via `--private-key` command-line argument - (File: framework/src/main/java/org/tron/core/config/args/CLIParameter.java)

### Summary
java-tron's `FullNode` startup accepts a raw witness signing private key directly as a command-line argument (`-p`/`--private-key`), mirroring the exact bug class described in the Peggo report (`--eth-pk`). Any local process can read this key via `/proc/<pid>/cmdline` (or `ps`), since Linux by default does not restrict cross-user visibility of process arguments unless `hidepid=2` is explicitly configured.

### Finding Description
`CLIParameter` declares the witness private key as a plaintext CLI option: [1](#0-0) 

This value flows unmodified into `Args.initLocalWitnesses`, which is the primary/first path checked for witness key material — taking precedence even over the config-file and keystore paths: [2](#0-1) 

From there it is passed to `WitnessInitializer.initFromCLIPrivateKey`, which stores it in `LocalWitnesses` in plaintext, for use in block signing: [3](#0-2) 

A typical witness node launch would be `java -jar FullNode.jar --witness -p <PRIVATE_KEY> ...`. On any multi-tenant or shared Linux host, this argument is visible to every local user via `/proc/<pid>/cmdline`, `ps -ef`, `ps aux`, or tools like `pspy`, unless the operator has manually enabled `hidepid=2,gid=0` on the `/proc` mount — a non-default hardening step. This is the identical root cause as the reported Peggo `--eth-pk` issue: a sensitive signing key passed as a CLI argument instead of being read from a file, keystore, or interactive prompt.

Notably, the project already recognizes and has partially remediated this exact bug class elsewhere: the `Toolkit.jar keystore import` command explicitly avoids passing private keys via CLI arguments, instead requiring `--key-file` or an interactive masked prompt via `Console.readPassword`: [4](#0-3) 

However, the witness startup flow (`FullNode.jar -p ...`) still allows and documents the insecure pattern (`--password` is likewise plaintext-on-CLI), and it is presented as a legitimate option in `CLIParameter`.

### Impact Explanation
The witness private key is used to sign blocks produced by the witness/SR (super representative) node — compromise of this key allows an attacker to impersonate the witness, sign malicious/conflicting blocks, or otherwise abuse the account's authority within consensus (equivocation, unauthorized transactions from that account, potential slashing/reputational damage to the SR). This qualifies as a concrete auth-impact: exposure of a signing credential used for consensus/authentication, not merely a theoretical issue, given the well-known accessibility of `/proc/<pid>/cmdline` to unprivileged local users on unhardened systems.

### Likelihood Explanation
Requires local unprivileged access to a host running a java-tron witness node with `-p`/`--private-key` — this is a common operational pattern for quick/manual witness startup or scripted deployments (systemd units, docker entrypoints, CI/CD secrets injected as CLI args), all of which commonly leak into shell history and process tables. Given `hidepid=2` is not the default on most distros, and the option is a first-class, undeprecated part of `CLIParameter`, likelihood is realistic in shared or containerized multi-tenant deployments.

### Recommendation
Deprecate and remove the `-p`/`--private-key` and `--password` CLI options from `CLIParameter`. Direct witness operators to use the existing keystore-file + password-prompt path (`initFromKeystore`) or a config-file / environment-injected secret read once at startup, following the same pattern already implemented in `KeystoreImport` (file-based input or masked console prompt, never argv). At minimum, emit a strong deprecation warning (as already done for other deprecated CLI flags via `DEPRECATED_CLI_TO_CONFIG`) and document the `/proc` exposure risk.

### Proof of Concept
1. Start a witness node: `java -jar FullNode.jar --witness -p 1a2b3c...deadbeef --witness-address <addr>`.
2. As a different unprivileged local user on the same host: `cat /proc/$(pgrep -f FullNode.jar)/cmdline | tr '\0' ' '` or `ps -eo pid,args | grep FullNode`.
3. The full private key is visible in plaintext, satisfying the same exploit scenario described in the Peggo report (`pspy`-style local process inspection).

### Citations

**File:** framework/src/main/java/org/tron/core/config/args/CLIParameter.java (L41-42)
```java
  @Parameter(names = {"-p", "--private-key"}, description = "Witness private key")
  public String privateKey;
```

**File:** framework/src/main/java/org/tron/core/config/args/Args.java (L899-911)
```java
  private static void initLocalWitnesses(Config config, CLIParameter cmd) {
    // not a witness node, skip
    if (!PARAMETER.isWitness()) {
      localWitnesses = new LocalWitnesses();
      return;
    }

    // path 1: CLI --private-key
    if (StringUtils.isNotBlank(cmd.privateKey)) {
      localWitnesses = WitnessInitializer.initFromCLIPrivateKey(
          cmd.privateKey, cmd.witnessAddress);
      return;
    }
```

**File:** framework/src/main/java/org/tron/core/config/args/WitnessInitializer.java (L24-43)
```java
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
