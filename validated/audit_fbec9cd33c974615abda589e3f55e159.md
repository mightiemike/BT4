### Title
Concurrent re-broadcast of an identical signed Transaction bypasses both the in-memory dup cache and `Manager.validateDup`, causing double execution before block commit - (File: framework/src/main/java/org/tron/core/Wallet.java)

### Summary
`Wallet.broadcastTransaction` only guards against replay using a size-bounded Guava cache (`dbManager.getTransactionIdCache()`), gated by `trxCacheEnable` and subject to eviction; the deeper guard, `Manager.validateDup`/`containsTransaction`, only consults `TransactionCache` (bloom filter) and `TransactionStore`, both of which are populated only when a transaction is committed into a block, not when it is merely accepted into `pendingTransactions`. An attacker can broadcast the same signed transaction twice concurrently and have it processed (and its state effects applied) twice before either copy is packed into a block.

### Finding Description
`Wallet.broadcastTransaction` performs a soft dup check: [1](#0-0) 
This check is skipped entirely when `trxCacheEnable=false`, and even when enabled it is a bounded Guava cache subject to eviction under churn, so it provides no hard guarantee.

The transaction is then handed to `Manager.pushTransaction`, which enqueues it, validates the signature, and — under `synchronized(transactionLock)` / `synchronized(this)` — calls `processTransaction`, which (per the code structure observed via `validateDup`/`containsTransaction` and the `if (blockCap == null) validateDup(trxCap)` gate used for directly-pushed, non-block transactions) invokes: [2](#0-1) 

`containsTransaction` only returns `true` if the bloom-filter-backed `TransactionCache` "might contain" the id (falling through to `TransactionStore.has`), and both of these persistent structures are populated when a block containing the transaction is committed — not merely when the transaction is admitted to the pending pool via `pushTransaction`: [3](#0-2) 

Because `pushTransaction` only adds the capsule to `pendingTransactions` (line 936) and never touches `TransactionCache`/`TransactionStore` at push time, a second, concurrent (or merely fast, back-to-back) `pushTransaction` call for the identical signed transaction sees `containsTransaction` return `false` again, `validateDup` passes a second time, and `processTransaction` re-executes the transaction's actuator against the already-mutated account state (the first transaction's effects were already merged into the session store via `tmpSession.merge()` before the lock was released). If the sender's balance/resource allows it, the transaction settles twice.

The bloom filter itself is explicitly documented as only usable to prove non-existence, not existence: [4](#0-3) 
and it is populated on commit, per `TxCacheDB.put(key, value)` which is driven by block/commit-time writes rather than pending-pool admission.

### Impact Explanation
A single signed transaction (transfer, approval, withdrawal, etc.) can be applied twice against chain state if an attacker races two broadcasts of the identical signed bytes before either is included in a block. This breaks the "exactly-once settlement" invariant and can cause double debit/credit of balances, double consumption of allowances, or double application of any one-time state transition, as long as post-first-execution account state still permits the second execution (e.g., sufficient balance/resources for both).

### Likelihood Explanation
Feasibility depends on winning a narrow race: both `pushTransaction` calls must reach `processTransaction`/`validateDup` before either transaction is committed into a block (i.e., before `TransactionCache`/`TransactionStore` reflect it). This is plausible because pending-pool residency can span multiple block intervals under load, and there is no dedup against `pendingTransactions` itself in `pushTransaction`/`containsTransaction`. The attacker fully controls broadcast timing and does not need any privileged access — this is a standard public broadcast API (`BroadcastServlet`, `BroadcastHexServlet`, gRPC). The `trxCacheEnable`/cache-eviction precondition in the question is not even strictly required, since the Guava cache in `Wallet` is not the only gap — the deeper `Manager.containsTransaction` check is itself insufficient against pending (uncommitted) duplicates regardless of `trxCacheEnable`.

### Recommendation
Add a hard, lock-protected duplicate check against the in-memory pending pool (e.g., maintain a `Set<Sha256Hash>` of transaction IDs currently in `pendingTransactions`/`pushTransactionQueue`, checked and inserted atomically inside the same `synchronized(this)` block as `processTransaction`/`pendingTransactions.add`) so `validateDup`/`containsTransaction` also rejects a transaction ID already resident in the pending pool, not only ones already committed to `TransactionStore`/`TransactionCache`.

### Proof of Concept
```java
// Integration-style test sketch, added near ManagerMockTest / TxCacheDBTest
@Test
public void testDoubleBroadcastSameTransactionBeforeCommit() throws Exception {
  TransactionCapsule trx = buildSignedTransferTransaction(senderKey, receiver, amount);

  ExecutorService pool = Executors.newFixedThreadPool(2);
  CountDownLatch start = new CountDownLatch(1);
  List<Future<Boolean>> results = new ArrayList<>();
  for (int i = 0; i < 2; i++) {
    results.add(pool.submit(() -> {
      start.await();
      try {
        return dbManager.pushTransaction(trx); // same capsule/bytes both times
      } catch (DupTransactionException e) {
        return false;
      }
    }));
  }
  start.countDown();

  int successCount = 0;
  for (Future<Boolean> f : results) {
    if (f.get()) successCount++;
  }

  // Expected (invariant): only one push succeeds, one throws DupTransactionException
  Assert.assertEquals(1, successCount);

  // Also assert account state changed only once (e.g., balance deducted exactly `amount` once,
  // not twice), by reading AccountStore before/after.
}
```
Expected failure mode demonstrating the bug: both `pushTransaction` calls return `true` (no `DupTransactionException`), and the sender's balance is deducted twice for a single signed transaction, violating the one-time-settlement invariant.

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L558-566)
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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L886-953)
```java
  public boolean pushTransaction(final TransactionCapsule trx)
      throws ValidateSignatureException, ContractValidateException, ContractExeException,
      AccountResourceInsufficientException, DupTransactionException, TaposException,
      TooBigTransactionException, TransactionExpirationException,
      ReceiptCheckErrException, VMIllegalException, TooBigTransactionResultException {

    if (isShieldedTransaction(trx.getInstance()) && !chainBaseManager.getDynamicPropertiesStore()
        .supportShieldedTransaction()) {
      throw new ContractValidateException("ShieldedTransferContract is not supported.");
    }

    if (isExchangeTransaction(trx.getInstance())) {
      throw new ContractValidateException("ExchangeTransactionContract is rejected");
    }

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
            trx.setTrxTrace(null);
            pendingTransactions.add(trx);
            Metrics.gaugeInc(MetricKeys.Gauge.MANAGER_QUEUE, 1,
                    MetricLabels.Gauge.QUEUE_PENDING);
            tmpSession.merge();
          }
          if (isShieldedTransaction(trx.getInstance())) {
            shieldedTransInPendingCounts.incrementAndGet();
          }
        }
      }
    } finally {
      if (pushTransactionQueue.remove(trx)) {
        Metrics.gaugeInc(MetricKeys.Gauge.MANAGER_QUEUE, -1,
            MetricLabels.Gauge.QUEUE_QUEUED);
      }
    }
    return true;
  }
```

**File:** chainbase/src/main/java/org/tron/core/db2/common/TxCacheDB.java (L181-188)
```java
  @Override
  public byte[] get(byte[] key) {
    if (!bloomFilters[0].mightContain(key) && !bloomFilters[1].mightContain(key)) {
      return null;
    }
    // this means exist
    return FAKE_TRANSACTION;
  }
```
