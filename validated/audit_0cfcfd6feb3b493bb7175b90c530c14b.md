### Title
Unmetered concurrent signature-verification DoS via duplicate transaction re-broadcast before dedup check - ([File: framework/src/main/java/org/tron/core/db/Manager.java])

### Summary
`Manager.pushTransaction` enqueues a transaction and performs expensive ECDSA signature recovery (`trx.validateSignature`) before any duplicate-transaction check occurs, and the duplicate check itself (`validateDup`/`containsTransaction`) only detects transactions already committed to a block, not transactions still in-flight in `pendingTransactions`/`pushTransactionQueue`. An unprivileged attacker can concurrently re-broadcast many copies of the same already-signed, not-yet-included transaction via the gRPC `BroadcastTransaction` endpoint, forcing the node to redundantly perform full signature verification for every copy before any rejection occurs.

### Finding Description
`Wallet.broadcastTransaction` (framework/src/main/java/org/tron/core/Wallet.java:507-575) constructs a fresh `TransactionCapsule` per RPC call and, when `node.rpc.trxCacheEnable` is `false` (the documented default — see `NodeConfig.RpcConfig.trxCacheEnable = false` in `common/src/main/java/org/tron/core/config/args/NodeConfig.java:228`), performs **no** local duplicate check before calling `dbManager.pushTransaction(trx)` at line 575. [1](#0-0) 

`Manager.pushTransaction` then does:
```
pushTransactionQueue.add(trx);
...
if (!trx.validateSignature(...)) { throw ... }
synchronized (transactionLock) { ... processTransaction(trx, null); ... }
``` [2](#0-1) 

The expensive `trx.validateSignature(...)` call happens unconditionally and outside any lock, before `validateDup(trx)` (invoked much later, inside `processTransaction` at line 1540) ever runs. [3](#0-2) 

Crucially, `validateDup`/`containsTransaction` only consults `transactionCache` (a bloom filter populated from `RecentTransactionStore`, itself only updated on block inclusion via `updateRecentTransaction`) and `TransactionStore.has()` (also only populated post-inclusion): [4](#0-3) [5](#0-4) 

Neither `pendingTransactions` nor `pushTransactionQueue` is checked, so identical unconfirmed transactions submitted concurrently will **all** pass `validateDup` as well, since none of them are yet recorded in `TransactionCache`/`TransactionStore`. Because each RPC call to `broadcastTransaction` builds a brand-new `TransactionCapsule` object (`isVerified` starts `false` per-instance — see `TransactionCapsule.validatePubSignature`, which only skips re-verification `if (!isVerified)` on the *same* object instance), the per-instance signature-verification cache provides no protection across duplicate broadcasts of the same signed transaction. [6](#0-5) 

### Impact Explanation
This is a DoS via RPC-API impact class: an unprivileged remote client can force the node to spend CPU on full ECDSA signature recovery repeatedly for the same transaction, with no cost gate (no fee/bandwidth is consumed until after signature validation and `validateDup` both pass — see `consumeBandwidth` called after `validateDup`/`validateSignature` in `processTransaction`). At default config (`trxCacheEnable=false`), there's no cheap in-memory shortcut at all, so every duplicate broadcast incurs the full signature-verification cost, and duplicates can also transiently occupy multiple slots in `pendingTransactions`, consuming server memory/queue capacity governed only by `maxTransactionPendingSize`/`isTooManyPending`.

### Likelihood Explanation
The precondition is only having one legitimately signed transaction (attacker's own funded account, or any transaction the attacker can observe/replay before it lands in a block — e.g., their own broadcast tx). The attacker needs no privileged role, only the ability to send concurrent gRPC `BroadcastTransaction` calls with the same payload, which is trivially scriptable and repeatable at scale (thousands of concurrent copies), limited only by the attacker's own network throughput and the node's `maxHttpConnectNumber`/gRPC thread pool sizing.

### Recommendation
Perform a cheap duplicate check (e.g., against `pushTransactionQueue`, `pendingTransactions`, and a synchronized/atomic transaction-ID cache) **before** invoking `trx.validateSignature()` in `Manager.pushTransaction`, so identical in-flight transactions are rejected prior to expensive signature recovery. Additionally, make the `Wallet.broadcastTransaction` in-memory `transactionIdCache` check atomic (e.g., `Cache.asMap().putIfAbsent`) rather than a racy get-then-put, and consider enabling it by default.

### Proof of Concept
```java
// Concurrently broadcast N copies of the same signed transaction via gRPC BroadcastTransaction
// before it is included in a block, with node.rpc.trxCacheEnable = false (default).
ExecutorService pool = Executors.newFixedThreadPool(200);
Protocol.Transaction signedTx = /* one valid signed transfer transaction */;
long startCpu = threadMXBean.getCurrentThreadCpuTime(); // baseline single-call timing
CountDownLatch latch = new CountDownLatch(2000);
for (int i = 0; i < 2000; i++) {
  pool.submit(() -> {
    walletStub.broadcastTransaction(signedTx); // gRPC call to BroadcastTransaction
    latch.countDown();
  });
}
latch.await();
// Expected (per report): trx.validateSignature() executes ~2000 times (measurable via
// VERIFY_SIGN_LATENCY / MANAGER_QUEUE metrics or CPU profiling), rather than being
// short-circuited by an early dedup check on the first duplicate.
```
Assertion: signature-verification CPU time / invocation count scales linearly with the number of concurrent duplicate broadcasts rather than being capped at 1, because no dedup check precedes `trx.validateSignature()` in `Manager.pushTransaction` and `validateDup` cannot detect not-yet-included duplicates.

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L558-575)
```java
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
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L861-881)
```java
  void validateDup(TransactionCapsule transactionCapsule) throws DupTransactionException {
    if (containsTransaction(transactionCapsule)) {
      throw new DupTransactionException(String.format("dup trans : %s ",
          transactionCapsule.getTransactionId()));
    }
  }

  private boolean containsTransaction(TransactionCapsule transactionCapsule) {
    return containsTransaction(transactionCapsule.getTransactionId().getBytes());
  }


  private boolean containsTransaction(byte[] transactionId) {
    if (transactionCache != null && !transactionCache.has(transactionId)) {
      // using the bloom filter only determines non-existent transaction
      return false;
    }

    return chainBaseManager.getTransactionStore()
        .has(transactionId);
  }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L901-909)
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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L2044-2053)
```java
  public void updateRecentTransaction(BlockCapsule block) {
    List list = new ArrayList<>();
    block.getTransactions().forEach(capsule -> {
      list.add(capsule.getTransactionId().toString());
    });
    RecentTransactionItem item = new RecentTransactionItem(block.getNum(), list);
    chainBaseManager.getRecentTransactionStore().put(
            ByteArray.subArray(ByteArray.fromLong(block.getNum()), 6, 8),
            new BytesCapsule(JsonUtil.obj2Json(item).getBytes()));
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L650-680)
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
```
