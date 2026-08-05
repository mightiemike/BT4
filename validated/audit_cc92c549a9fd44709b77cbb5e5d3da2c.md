This confirms the critical piece: `chainBaseManager.getTransactionStore().put(...)` happens synchronously inside `processTransaction`, **before** the outer `pushTransaction` call returns, and it happens inside the `synchronized(this)` block that also runs `validateDup`. Since both threads serialize on `synchronized(transactionLock)` → `synchronized(this)`, the *second* thread's `processTransaction` call runs strictly after the first thread's `processTransaction` has already executed `chainBaseManager.getTransactionStore().put(txId, trxCap)` [1](#0-0) . Therefore `validateDup` → `containsTransaction` will see the transaction already present in `TransactionStore` (and/or the bloom-filter `transactionCache`) and throw `DupTransactionException` for the second thread [2](#0-1) .

### Title
No exploitable double-execution: `processTransaction`'s serialized dup-check under `transactionLock` prevents double settlement despite the Wallet-level cache race — (File: framework/src/main/java/org/tron/core/Wallet.java / framework/src/main/java/org/tron/core/db/Manager.java)

### Summary
The `Wallet.broadcastTransaction` guava-cache check-then-put (`getTransactionIdCache().getIfPresent`/`put`) is indeed racy and only active when `trxCacheEnable=true` (default `false`) [3](#0-2) [4](#0-3) . However, both racing calls still funnel into `Manager.pushTransaction`, which serializes all execution behind `synchronized(transactionLock)` and an inner `synchronized(this)` [5](#0-4) , and `processTransaction` writes the transaction to the persistent `TransactionStore` and bloom-filter `transactionCache` before releasing that lock [1](#0-0) . Consequently the second concurrent thread's subsequent `validateDup`/`containsTransaction` check will see the tx as already present and reject it with `DupTransactionException` [2](#0-1) .

### Finding Description
- `broadcastTransaction`'s cache dedup is a genuine TOCTOU race (`getIfPresent` then `put` are not atomic) [3](#0-2) , but this only gates whether the RPC layer *rejects early*; it does not gate correctness of settlement.
- Regardless of that race, every accepted call proceeds to `dbManager.pushTransaction(trx)` [6](#0-5) .
- `pushTransaction` validates the transaction signature *before* acquiring `transactionLock` (this part is unsynchronized and can run concurrently for both threads), but the actual balance-mutating work — `processTransaction(trx, null)` — only runs inside `synchronized (transactionLock) { synchronized (this) { ... } }` [5](#0-4) . This means the two threads' `processTransaction` calls are fully serialized, not concurrent.
- Inside `processTransaction`, `validateDup(trxCap)` is called (which checks `TransactionStore.has()` and the `transactionCache` bloom filter) [7](#0-6) , and by the time the second thread reaches this check, the first thread has already committed the txID to `TransactionStore` and `transactionCache` (lines 1582-1586), inside the same critical section, before returning. So the second call to `validateDup` throws `DupTransactionException`, and `AccountStore.adjustBalance` (or vote-count mutation) inside `TransferActuator`/`VoteWitnessActuator` executes only once.
- The claimed "CALL_SEQUENCE: two concurrent threads both passing the cache check before either puts the txID" is only true for the outer Wallet-level `transactionIdCache` (which is disabled by default and, when enabled, only produces a false negative on early rejection — not a double-settlement, since the downstream `Manager.pushTransaction`/`processTransaction` pair still serializes and dedupes correctly).

### Impact Explanation
No double-settlement occurs. The attacker can at most cause one of the two racing `broadcastTransaction` calls to return a `DUP_TRANSACTION_ERROR` (via either the Wallet cache or the `Manager.validateDup` path) — a benign UX/availability nuisance, not a balance/vote duplication.

### Likelihood Explanation
The described race condition at the Wallet-cache layer is real and reproducible, but it does not propagate into a violation of the "settle exactly once" invariant because `Manager.pushTransaction`'s locking (`transactionLock` + inner `this` monitor) combined with `validateDup`'s check against the already-persisted `TransactionStore`/`transactionCache` writes performed inside the very same critical section closes the gap. There is no code path in this repository version where two concurrent `pushTransaction(trx)` calls for the identical txID both pass `validateDup` and both execute `TransferActuator`/vote-mutating logic.

### Recommendation
Not applicable — no fix required for double-settlement. Optionally, for consistency/observability, the Wallet-level `getIfPresent`/`put` on `transactionIdCache` could be replaced with an atomic `Cache.asMap().putIfAbsent(...)` to avoid the harmless duplicate `DUP_TRANSACTION_ERROR` race, but this is a cosmetic hardening, not a security fix.

### Proof of Concept
Java concurrency test sketch (using a real `Manager`/`ChainBaseManager` test context, e.g. extending `BaseTest`/`ManagerTest` fixtures already in the repo, such as `framework/src/test/java/org/tron/core/db/ManagerTest.java`):
```java
@Test
public void testConcurrentPushTransactionSettlesOnce() throws Exception {
  // build a funded owner account and a single signed TransferContract trx (amount = X)
  TransactionCapsule trx = buildSignedTransferTrx(ownerKey, toAddress, X);

  int N = 8;
  ExecutorService pool = Executors.newFixedThreadPool(N);
  CountDownLatch start = new CountDownLatch(1);
  AtomicInteger successCount = new AtomicInteger(0);
  List<Future<?>> futures = new ArrayList<>();
  for (int i = 0; i < N; i++) {
    futures.add(pool.submit(() -> {
      start.await();
      try {
        if (dbManager.pushTransaction(trx)) {
          successCount.incrementAndGet();
        }
      } catch (DupTransactionException ignored) {
        // expected for all but one thread
      }
      return null;
    }));
  }
  start.countDown();
  for (Future<?> f : futures) { f.get(); }

  // Assert exactly one thread succeeded in applying the transaction
  Assert.assertEquals(1, successCount.get());

  // Assert the balance delta equals X, not N*X
  long finalBalance = dbManager.getAccountStore().get(ownerAddress).getBalance();
  Assert.assertEquals(initialBalance - X - fee, finalBalance);
}
```
Expected result: exactly one thread's `pushTransaction` returns `true`/applies the transfer; all others throw `DupTransactionException` from `validateDup`, confirming the invariant holds and no double-settlement occurs in this codebase.

### Citations

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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L911-945)
```java
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
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1540-1540)
```java
    validateDup(trxCap);
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1582-1586)
```java
    chainBaseManager.getTransactionStore().put(trxCap.getTransactionId().getBytes(), trxCap);

    Optional.ofNullable(transactionCache)
        .ifPresent(t -> t.put(trxCap.getTransactionId().getBytes(),
            new BytesCapsule(ByteArray.fromLong(trxCap.getBlockNum()))));
```

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

**File:** framework/src/main/java/org/tron/core/Wallet.java (L574-575)
```java
      trx.checkExpiration(chainBaseManager.getNextBlockSlotTime());
      dbManager.pushTransaction(trx);
```

**File:** common/src/main/resources/reference.conf (L323-323)
```text
    trxCacheEnable = false # Whether to enable transaction cache in broadcast transaction API(rpc and http).
```
