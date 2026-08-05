### Title
Fork-gated multisig weight dedup key change in `checkWeight` allows differential signature-malleability duplicate-counting during the VERSION_4_7_1 transition window - (chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java)

### Summary
`TransactionCapsule.checkWeight` dedupes multisig approvals using either the raw base64 signature bytes or `encode58Check(address)` depending on `ForkController.instance().pass(VERSION_4_7_1)`. Because pre-fork nodes key the dedup map by signature bytes rather than recovered address, an attacker holding one multisig key can submit two byte-distinct but same-address-recovering signatures (ECDSA signature malleability: `(r, s, v)` vs `(r, n-s, 1-v)`) so that nodes still on the old fork state count the weight twice while nodes that have passed the fork correctly reject it as "has signed twice," producing accept/reject divergence for the identical transaction across honest nodes during the activation window.

### Finding Description
`checkWeight` builds a `HashMap addMap` and, for each signature in the transaction, recovers the signer address via `SignUtils.signatureToAddress`, then chooses the dedup key: [1](#0-0) 

```
String base64 = TransactionCapsule.getBase64FromByteString(sig);
byte[] address = SignUtils.signatureToAddress(hash, base64, ...);
long weight = getWeight(permission, address);
...
if (ForkController.instance().pass(Parameter.ForkBlockVersionEnum.VERSION_4_7_1)) {
  base64 = encode58Check(address);
}
if (addMap.containsKey(base64)) {
  throw new PermissionException(encode58Check(address) + " has signed twice!");
}
addMap.put(base64, weight);
```

The only per-signature admission check is `sig.size() < 65` [2](#0-1) ; there is no canonical-signature (low-S) enforcement visible in the reachable code path, so two byte-distinct `(r,s,v)` encodings from the same private key over the same hash can both recover to the same address via standard secp256k1 malleability. Pre-fork, `addMap` is keyed by the raw base64 signature, so two malleable variants are treated as two independent signers and their weights are summed; post-fork, the key is `encode58Check(address)`, so the second variant is correctly rejected as a duplicate signer.

This function is on the consensus-critical validation path, not just an off-chain RPC helper: `checkWeight` is invoked from `TransactionCapsule.validateSignature` [3](#0-2) , which is called by `validatePubSignature`/`validateSignature` [4](#0-3) , which is invoked in `Manager.pushTransaction` and `Manager.processTransaction` for every incoming transaction, both broadcast and block-application paths [5](#0-4) [6](#0-5) . This is reachable by any unprivileged user who owns (or has been given weight in) a multisig `Permission`, requiring no admin/governance access — an ordinary multisig account owner submitting a transaction is enough.

`ForkController.pass` itself is a per-node, non-atomically-activated value driven by witness voting and block timestamps [7](#0-6) , meaning different nodes in the network can legitimately observe different values for `pass(VERSION_4_7_1)` for a period during rollout, which is exactly the "mid-fork-transition" precondition in the question.

### Impact Explanation
During the transition window where some nodes have activated `VERSION_4_7_1` and some have not, a single transaction from a multisig account, signed once with a key and duplicated via ECDSA signature malleability, can be:
- Accepted by nodes that have not passed the fork (their `currentWeight` counts both malleable signatures, potentially reaching threshold with effectively one real distinct signer), and
- Rejected by nodes that have passed the fork (`PermissionException: ... has signed twice!`).

This is a consensus-determinism violation: identical transaction input yields different `checkWeight`/`validateSignature` outcomes across honest nodes, which can cause block/transaction acceptance divergence and potential chain-split behavior strictly confined to the fork-activation transition window. It does not persist once all nodes have passed the fork, and it does not let an attacker forge signatures from keys they do not control — it only inflates the effective vote weight of a single controlled key during the transition.

### Likelihood Explanation
Feasibility is high in principle (ECDSA signature malleability is a standard, well-understood technique) but the practical window is narrow and precondition-heavy: (1) the network must be actively mid-rollout of `VERSION_4_7_1` such that node views of `ForkController.pass()` diverge, (2) the attacker must control a key with nonzero weight in a multisig `Permission`, and (3) the resulting inflated weight must be enough to cross the permission `threshold` when combined with other required signatures. This is a transition-window-only issue that self-resolves once the fork activates network-wide, which is consistent with why the fork was introduced in the first place (hardening the dedup key from signature-based to address-based).

### Recommendation
Do not gate the dedup key choice on a globally-inconsistent, time-varying oracle (`ForkController.pass`) for a decision that must be block-height/timestamp deterministic across all nodes evaluating the same transaction; ensure dedup by recovered address is unconditionally used once the corresponding hard-fork block height is deterministically reached for all validators, and audit whether other similarly `ForkController`-gated logic in consensus-critical paths (e.g., `checkCPUTime`, `checkCPUTimeForCreate2` in `MUtil.java`) has the same transitional-divergence property. Additionally, add explicit low-S/canonical signature enforcement in `checkWeight`'s signature admission check so malleable signature variants are rejected outright regardless of fork state.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/capsule/CheckWeightForkDivergenceTest.java
@Test
public void testCheckWeightDivergesAcrossForkBoundary() throws Exception {
  ECKey ecKey = new ECKey(Utils.getRandom());
  Permission permission = Permission.newBuilder()
      .setThreshold(2)
      .addKeys(Key.newBuilder().setAddress(ByteString.copyFrom(ecKey.getAddress())).setWeight(1))
      .build();

  byte[] hash = Sha256Hash.hash(true, "test".getBytes());
  ECKey.ECDSASignature sig1 = ecKey.sign(hash);
  // Malleable variant: s' = N - s, recId flips accordingly, same recovered address
  ECKey.ECDSASignature sig2 = new ECKey.ECDSASignature(sig1.r, ECKey.CURVE.getN().subtract(sig1.s));
  byte[] sigBytes1 = /* build 65-byte r||s||v for sig1 */;
  byte[] sigBytes2 = /* build 65-byte r||s||(1-v) for sig2, verified to recover to ecKey.getAddress() */;

  List<ByteString> sigs = Arrays.asList(
      ByteString.copyFrom(sigBytes1), ByteString.copyFrom(sigBytes2));

  // Simulate pre-fork node (dedup by base64 signature)
  ForkUtils.forcePass(false); // test hook / mock ForkController.instance().pass(...) == false
  long preForkWeight = TransactionCapsule.checkWeight(permission, sigs, hash, new ArrayList<>());
  Assert.assertEquals(2, preForkWeight); // BUG: both malleable sigs counted -> reaches threshold

  // Simulate post-fork node (dedup by encode58Check(address))
  ForkUtils.forcePass(true);
  try {
    TransactionCapsule.checkWeight(permission, sigs, hash, new ArrayList<>());
    Assert.fail("expected PermissionException: has signed twice");
  } catch (PermissionException e) {
    Assert.assertTrue(e.getMessage().contains("has signed twice"));
  }
  // preForkWeight (2) >= threshold (2) while post-fork throws -> divergent accept/reject
}
```
Expected assertions: pre-fork path returns `currentWeight == 2` (threshold met, transaction accepted), post-fork path throws `PermissionException` (transaction rejected) for the exact same `sigs`/`hash`/`permission` input, demonstrating the divergence described.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L243-247)
```java
    for (ByteString sig : sigs) {
      if (sig.size() < 65) {
        throw new SignatureFormatException(
            "Signature size is " + sig.size());
      }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L248-263)
```java
      String base64 = TransactionCapsule.getBase64FromByteString(sig);
      byte[] address = SignUtils
          .signatureToAddress(hash, base64, CommonParameter.getInstance().isECKeyCryptoEngine());
      long weight = getWeight(permission, address);
      if (weight == 0) {
        throw new PermissionException(
            ByteArray.toHexString(hash) + " is signed by " + encode58Check(address)
                + " but it is not contained of permission.");
      }
      if (ForkController.instance().pass(Parameter.ForkBlockVersionEnum.VERSION_4_7_1)) {
        base64 = encode58Check(address);
      }
      if (addMap.containsKey(base64)) {
        throw new PermissionException(encode58Check(address) + " has signed twice!");
      }
      addMap.put(base64, weight);
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L468-496)
```java
  public static boolean validateSignature(Transaction transaction,
      byte[] hash, AccountStore accountStore, DynamicPropertiesStore dynamicPropertiesStore)
      throws PermissionException, SignatureException, SignatureFormatException {
    Transaction.Contract contract = transaction.getRawData().getContractList().get(0);
    int permissionId = contract.getPermissionId();
    byte[] owner = getOwner(contract);
    AccountCapsule account = accountStore.get(owner);
    Permission permission = null;
    if (account == null) {
      if (permissionId == 0) {
        permission = AccountCapsule.getDefaultPermission(ByteString.copyFrom(owner));
      }
      if (permissionId == 2) {
        permission = AccountCapsule
            .createDefaultActivePermission(ByteString.copyFrom(owner), dynamicPropertiesStore);
      }
    } else {
      permission = account.getPermissionById(permissionId);
    }
    if (permission == null) {
      throw new PermissionException("permission isn't exit");
    }
    checkPermission(permissionId, permission, contract);
    long weight = checkWeight(permission, transaction.getSignatureList(), hash, null);
    if (weight >= permission.getThreshold()) {
      return true;
    }
    return false;
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L650-719)
```java
  public boolean validatePubSignature(AccountStore accountStore,
      DynamicPropertiesStore dynamicPropertiesStore)
      throws ValidateSignatureException {
    if (!isVerified) {
      if (this.transaction.getSignatureCount() <= 0
              || this.transaction.getRawData().getContractCount() <= 0) {
        throw new ValidateSignatureException("miss sig or contract");
      }
      if (this.transaction.getSignatureCount() > dynamicPropertiesStore
              .getTotalSignNum()) {
        throw new ValidateSignatureException("too many signatures");
      }

      byte[] hash = getTransactionId().getBytes();

      long startNs = System.nanoTime();
      try {
        if (!validateSignature(this.transaction, hash, accountStore, dynamicPropertiesStore)) {
          isVerified = false;
          throw new ValidateSignatureException("sig error");
        }
      } catch (SignatureException | PermissionException | SignatureFormatException e) {
        isVerified = false;
        throw new ValidateSignatureException(e.getMessage());
      } finally {
        logSlowSigVerify(startNs);
      }
      isVerified = true;
    }
    return true;
  }

  /**
   * WARN-logs when a single signature verification exceeds
   * {@link #SLOW_SIG_VERIFY_MS}. Package-private so it can be exercised from
   * tests without forcing a real slow crypto path.
   */
  void logSlowSigVerify(long startNs) {
    long costMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startNs);
    if (costMs > SLOW_SIG_VERIFY_MS) {
      logger.warn("slow verify: txId={}, sigCount={}, cost={} ms",
          getTransactionId(), this.transaction.getSignatureCount(), costMs);
    }
  }

  /**
   * validate signature
   */
  public boolean validateSignature(AccountStore accountStore,
      DynamicPropertiesStore dynamicPropertiesStore) throws ValidateSignatureException {
    if (!isVerified) {
      //Do not support multi contracts in one transaction
      Transaction.Contract contract = this.getInstance().getRawData().getContract(0);
      if (contract.getType() != ContractType.ShieldedTransferContract) {
        validatePubSignature(accountStore, dynamicPropertiesStore);
      } else {  //ShieldedTransfer
        byte[] owner = getOwnerAddress();
        if (!ArrayUtils.isEmpty(owner)) { //transfer from transparent address
          validatePubSignature(accountStore, dynamicPropertiesStore);
        } else { //transfer from shielded address
          if (this.transaction.getSignatureCount() > 0) {
            throw new ValidateSignatureException("there should be no signatures signed by "
                    + "transparent address when transfer from shielded address");
          }
        }
      }
      isVerified = true;
    }
    return true;
  }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L905-909)
```java
      if (!trx.validateSignature(chainBaseManager.getAccountStore(),
          chainBaseManager.getDynamicPropertiesStore())) {
        throw new ValidateSignatureException(String.format("trans sig validate failed, id: %s",
            trx.getTransactionId()));
      }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1542-1546)
```java
    if (!trxCap.validateSignature(chainBaseManager.getAccountStore(),
        chainBaseManager.getDynamicPropertiesStore())) {
      throw new ValidateSignatureException(
          String.format(" %s transaction signature validate failed", txId));
    }
```

**File:** chainbase/src/main/java/org/tron/common/utils/ForkController.java (L79-104)
```java
  private boolean passNew(int version) {
    ForkBlockVersionEnum versionEnum = ForkBlockVersionEnum.getForkBlockVersionEnum(version);
    if (versionEnum == null) {
      logger.warn("Not exist block version: {}.", version);
      return false;
    }
    long latestBlockTime = manager.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    long maintenanceTimeInterval = manager.getDynamicPropertiesStore().getMaintenanceTimeInterval();
    long hardForkTime = ((versionEnum.getHardForkTime() - 1) / maintenanceTimeInterval + 1)
        * maintenanceTimeInterval;
    if (latestBlockTime < hardForkTime) {
      return false;
    }
    byte[] stats = manager.getDynamicPropertiesStore().statsByVersion(version);
    if (stats == null || stats.length == 0) {
      return false;
    }
    int count = 0;
    for (int i = 0; i < stats.length; i++) {
      if (check[i] == stats[i]) {
        ++count;
      }
    }
    return count >= ceil((double) versionEnum.getHardForkRate() * stats.length / 100,
        manager.getDynamicPropertiesStore().disableJavaLangMath());
  }
```
