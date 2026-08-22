### Title
TOCTOU permission-revocation bypass via cached `isVerified` flag in `Manager.pushTransaction`/`processTransaction` - ([File: framework/src/main/java/org/tron/core/db/Manager.java])

### Summary
`Manager.pushTransaction` calls `trx.validateSignature(...)` at line 905 **before** acquiring `transactionLock`/`synchronized(this)`, caching `TransactionCapsule.isVerified = true`. When the transaction later reaches `processTransaction` (line 1542), the signature/permission check is skipped because `isVerified` is already `true`, even if an `AccountPermissionUpdateContract` from the same account committed in between and changed (or revoked) the signer's permission weight. This lets a transaction execute against stale authorization state.

### Finding Description
`TransactionCapsule.validateSignature` short-circuits when `isVerified` is already `true`: [1](#0-0) 

`Manager.pushTransaction` performs the first signature/permission check **outside** any synchronization: [2](#0-1) 

`Manager.processTransaction`, invoked inside `synchronized(this)`, re-invokes `validateSignature`, but this call is a no-op due to the cached flag: [3](#0-2) 

Because gRPC/HTTP transaction broadcast (`Wallet.broadcastTransaction` → `dbManager.pushTransaction`) is handled by a multi-threaded server, two independently-broadcast transactions from the same account can race: [4](#0-3) 

Exploit flow:
1. Account has a multi-signer permission (e.g. `Active` permission with threshold satisfiable by a single co-signer key `K1`).
2. Attacker (a legitimate co-signer holding `K1`, about to be stripped of authorization) broadcasts transfer tx `A` signed with `K1`.
3. Concurrently, the account owner broadcasts `AccountPermissionUpdateContract` tx `B` that removes `K1`'s weight/permission.
4. If thread scheduling allows tx `B` to fully validate and commit (inside its own `synchronized` region) after tx `A`'s unlocked `validateSignature()` call (line 905) but before tx `A` acquires `transactionLock`/enters `processTransaction`, tx `A`'s cached `isVerified=true` survives into `processTransaction`, and the transfer executes despite `K1` no longer holding sufficient permission weight at the moment of state mutation.
5. No other check (`validateTapos`, `validateCommon`, `validateDup`, or the transfer actuator's `validate()`) re-derives permission state; permission checking exists solely inside `validateSignature`, which is bypassed by the cache.

### Impact Explanation
This is an unauthorized account operation: a transaction executes and mutates account balances using a signing permission that has already been revoked at the time of execution, violating the invariant that authorization must be valid at the moment of state mutation, not merely at initial admission to the mempool. This maps to the "unauthorized account operations / asset accounting corruption" bounty impact class.

### Likelihood Explanation
This requires: (a) an account permission configuration with an at-risk co-signer whose weight is being revoked, and (b) the attacker (co-signer) racing their own transfer against the revocation transaction with fine-grained timing. It requires no leaked keys — only the attacker's own legitimately-held (but soon-to-be-revoked) signing weight. The race window is narrow (between an unlocked crypto-verification call and lock acquisition) but is real and repeatable by flooding transfer broadcasts around the anticipated revocation time; success is probabilistic rather than deterministic, which somewhat lowers likelihood but does not eliminate the underlying design flaw.

### Recommendation
Re-validate signature/permission state unconditionally (or re-derive permission weight against current `AccountStore` state) inside `processTransaction` while holding the lock, rather than trusting a cached `isVerified` flag set outside the critical section. Alternatively, move the initial `validateSignature` call in `pushTransaction` inside the same locked region as `processTransaction`, or force `isVerified=false` before entering `processTransaction` for transactions taken from `pushTransactionQueue` that have not yet been committed under lock.

### Proof of Concept
```java
@Test
public void testStalePermissionCacheBypassesReValidation() throws Exception {
  // 1. Build account with Active permission satisfied by key K1, threshold reachable alone.
  ECKey k1 = new ECKey(Utils.getRandom());
  byte[] owner = ... ; // account address with permission including k1
  AccountCapsule account = ...; // configured with active permission incl. k1 at sufficient weight
  chainManager.getAccountStore().put(owner, account);

  // 2. Build transfer tx A signed only by K1.
  TransactionCapsule trxA = transfer(owner, someTarget, 1L, ..., expiration);
  trxA.sign(k1.getPrivKeyBytes());

  // 3. Simulate pushTransaction's early check: cache isVerified=true against CURRENT (pre-revoke) state.
  trxA.validateSignature(chainManager.getAccountStore(), chainManager.getDynamicPropertiesStore());
  Assert.assertTrue((Boolean) ReflectUtils.getFieldObject(trxA, "isVerified"));

  // 4. Simulate concurrent AccountPermissionUpdateContract committing and removing K1 from the account's permission.
  AccountCapsule updated = chainManager.getAccountStore().get(owner);
  updated.updatePermissions(...); // remove k1 / drop weight below threshold
  chainManager.getAccountStore().put(owner, updated);

  // 5. processTransaction should reject trxA now that K1 lacks permission,
  //    but because isVerified is cached true, it proceeds and executes the transfer.
  TransactionInfo info = dbManager.processTransaction(trxA, null);
  Assert.assertNull("Expected transfer to be rejected due to revoked permission, but it executed", info);
}
```
Expected (secure) behavior: `processTransaction` throws `ValidateSignatureException` because `K1` no longer satisfies the account's permission threshold. Actual (vulnerable) behavior: the transfer executes because `trxA.validateSignature(...)` at line 1542 short-circuits on the cached `isVerified=true`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L698-719)
```java
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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L900-934)
```java

    pushTransactionQueue.add(trx);
    Metrics.gaugeInc(MetricKeys.Gauge.MANAGER_QUEUE, 1,
        MetricLabels.Gauge.QUEUE_QUEUED);
    try {
      if (!trx.validateSignature(chainBaseManager.getAccountStore(),
          chainBaseManager.getDynamicPropertiesStore())) {
        throw new ValidateSignatureException(String.format("trans sig validate failed, id: %s",
            trx.getTransactionId()));
      }

      synchronized (transactionLock) {
        while (true) {
          try {
            if (isBlockWaitingLock()) {
              TimeUnit.MILLISECONDS.sleep(SLEEP_FOR_WAIT_LOCK);
            } else {
              break;
            }
          } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            logger.debug("The wait has been interrupted.");
          }
        }
        synchronized (this) {
          if (isShieldedTransaction(trx.getInstance())
                  && shieldedTransInPendingCounts.get() >= shieldedTransInPendingMaxCounts) {
            return false;
          }
          if (!session.valid()) {
            session.setValue(revokingStore.buildSession());
          }

          try (ISession tmpSession = revokingStore.buildSession()) {
            processTransaction(trx, null);
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1537-1546)
```java
    validateTapos(trxCap);
    validateCommon(trxCap);

    validateDup(trxCap);

    if (!trxCap.validateSignature(chainBaseManager.getAccountStore(),
        chainBaseManager.getDynamicPropertiesStore())) {
      throw new ValidateSignatureException(
          String.format(" %s transaction signature validate failed", txId));
    }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L507-576)
```java
  public GrpcAPI.Return broadcastTransaction(Transaction signedTransaction) {
    GrpcAPI.Return.Builder builder = GrpcAPI.Return.newBuilder();
    TransactionCapsule trx = new TransactionCapsule(signedTransaction);
    trx.setTime(System.currentTimeMillis());
    Sha256Hash txID = trx.getTransactionId();
    try {
      for (ByteString sig : signedTransaction.getSignatureList()) {
        if (!SignUtils.isValidLength(sig.size())) {
          String info = "Signature size is " + sig.size();
          logger.warn("Broadcast transaction {} has failed, {}.", txID, info);
          return builder.setResult(false).setCode(response_code.SIGERROR)
              .setMessage(ByteString.copyFromUtf8("Validate signature error: " + info))
              .build();
        }
      }

      if (tronNetDelegate.isBlockUnsolidified()) {
        logger.warn("Broadcast transaction {} has failed, block unsolidified.", txID);
        return builder.setResult(false).setCode(response_code.BLOCK_UNSOLIDIFIED)
          .setMessage(ByteString.copyFromUtf8("Block unsolidified."))
          .build();
      }

      if (minEffectiveConnection != 0) {
        if (tronNetDelegate.getActivePeer().isEmpty()) {
          logger.warn("Broadcast transaction {} has failed, no connection.", txID);
          return builder.setResult(false).setCode(response_code.NO_CONNECTION)
              .setMessage(ByteString.copyFromUtf8("No connection."))
              .build();
        }

        int count = (int) tronNetDelegate.getActivePeer().stream()
            .filter(p -> !p.isNeedSyncFromUs() && !p.isNeedSyncFromPeer())
            .count();

        if (count < minEffectiveConnection) {
          String info = "Effective connection:" + count + " lt minEffectiveConnection:"
              + minEffectiveConnection;
          logger.warn("Broadcast transaction {} has failed. {}.", txID, info);
          return builder.setResult(false).setCode(response_code.NOT_ENOUGH_EFFECTIVE_CONNECTION)
              .setMessage(ByteString.copyFromUtf8(info))
              .build();
        }
      }

      if (dbManager.isTooManyPending()) {
        logger.warn("Broadcast transaction {} has failed, too many pending.", txID);
        return builder.setResult(false).setCode(response_code.SERVER_BUSY)
            .setMessage(ByteString.copyFromUtf8("Server busy.")).build();
      }

      if (trxCacheEnable) {
        if (dbManager.getTransactionIdCache().getIfPresent(txID) != null) {
          logger.warn("Broadcast transaction {} has failed, it already exists.", txID);
          return builder.setResult(false).setCode(response_code.DUP_TRANSACTION_ERROR)
              .setMessage(ByteString.copyFromUtf8("Transaction already exists.")).build();
        } else {
          dbManager.getTransactionIdCache().put(txID, true);
        }
      }

      if (chainBaseManager.getDynamicPropertiesStore().supportVM()) {
        trx.resetResult();
      }
      if (trx.getInstance().getRawData().getContractCount() == 0) {
        throw new ContractValidateException(ActuatorConstant.CONTRACT_NOT_EXIST);
      }
      trx.checkExpiration(chainBaseManager.getNextBlockSlotTime());
      dbManager.pushTransaction(trx);
      TransactionMessage message = new TransactionMessage(trx.getInstance().toByteArray());
```
