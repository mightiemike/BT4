### Title
Race between concurrent `TransactionsMessage` batches allows duplicate `pushTransaction` execution before commit - ([File: framework/src/main/java/org/tron/core/net/messagehandler/TransactionsMsgHandler.java])

### Summary
`TransactionsMsgHandler.check()` only rejects duplicate transaction hashes **within a single** `TransactionsMessage`, and `handleTransaction()`'s only pre-push dedup guard is `advService.getMessage(item) == null`, which is populated only *after* `tronNetDelegate.pushTransaction(trx)` succeeds and `advService.broadcast(trx)` runs. An attacker controlling a peer can send the same transaction in two separate `TransactionsMessage` batches back-to-back so that both invocations of `handleTransaction` observe `advService.getMessage() == null` and both reach `tronNetDelegate.pushTransaction(trx)`.

### Finding Description
`processMessage()` calls `check(peer, transactionsMessage)` which builds a per-call `HashSet<Sha256Hash> seen` and only throws `P2pException` for duplicates found inside the same `list` [1](#0-0) . It performs no cross-message state tracking, so a duplicate transaction split across two consecutive `TransactionsMessage` objects from the same (or different) peer passes `check()` twice.

Each transaction is then dispatched asynchronously to `trxHandlePool` (or via `smartContractQueue`/`smartContractExecutor`), invoking `handleTransaction(peer, trx)` [2](#0-1) . The only dedup gate before calling `tronNetDelegate.pushTransaction(trx.getTransactionCapsule())` is:
```java
if (advService.getMessage(new Item(trx.getMessageId(), InventoryType.TRX)) != null) {
  return;
}
``` [3](#0-2) 
`advService.broadcast(trx)` (which populates the seen-item cache used by `getMessage`) only runs *after* `pushTransaction` returns successfully. If two threads from the pool execute `handleTransaction` for the identical `trx` nearly simultaneously, both can read `getMessage() == null` before either has broadcast/cached it, so both proceed to call `tronNetDelegate.pushTransaction(trx.getTransactionCapsule())` [4](#0-3) .

`TronNetDelegate.pushTransaction` forwards to `Manager.pushTransaction`, which serializes mutation via `synchronized (transactionLock) { synchronized (this) { ... } }` [5](#0-4) . This serialization means the two calls do not corrupt in-memory data structures, but it does **not** guarantee the second call is rejected as a duplicate: `Manager.validateDup()` only checks `containsTransaction()`, which tests the bloom-filter `transactionCache` and `chainBaseManager.getTransactionStore().has(transactionId)` — i.e., transactions already **persisted/committed** to a block [6](#0-5) . A transaction that is merely *pending* (already added to `pendingTransactions` by the first, still-in-flight `pushTransaction` call) is not yet in the transaction store, so `validateDup` does not detect it. The `pushTransaction` body itself performs `processTransaction(trx, null)` then `pendingTransactions.add(trx)` unconditionally within the synchronized block, with no check against `pendingTransactions` for an already-present identical transaction id [7](#0-6) . Because TRON transactions have no per-account nonce (uniqueness enforced only by tx-id dedup / tapos+expiration), the second serialized `processTransaction` call executes against account state already mutated by the first (via `tmpSession.merge()`), effectively re-applying the identically signed transaction a second time.

### Impact Explanation
If the account has sufficient balance/resources for both executions, the same signed transaction (e.g., a transfer or contract call) could be applied twice to pending state before either is included in and later deduplicated by block production, resulting in double-application of a single user-authorized action. This is a duplication/replay risk against the invariant that "one transaction must be admitted into pending exactly once."

### Likelihood Explanation
Requires only that an attacker control (or heavily influence timing to) a peer connection able to send two `TransactionsMessage` batches for the same transaction in quick succession, exploiting a benign timing window between the two `handleTransaction` invocations checking `advService.getMessage()` and the later `advService.broadcast()` call that populates it. This is a narrow race window (microseconds, bound by thread-pool scheduling) but is deterministically reproducible in a unit test that directly invokes `handleTransaction` twice concurrently before `broadcast` completes.

### Recommendation
Add an explicit, atomic "claim" step before dispatching to `pushTransaction` — e.g., use `advService`'s inventory cache (or a dedicated `ConcurrentHashMap`/`Set` keyed by tx id with `putIfAbsent`) to mark a transaction as "in-flight" the moment it is accepted for processing (not merely after successful broadcast), and skip if already claimed. Additionally, have `Manager.pushTransaction`/`validateDup` check `pendingTransactions` (or an equivalent pending-id index) in addition to the committed `transactionStore`.

### Proof of Concept
```java
@Test
public void testConcurrentDuplicateTransactionRace() throws Exception {
  TransactionCapsule trx = ...; // same signed transaction instance/bytes
  TransactionsMsgHandler handler = new TransactionsMsgHandler();
  // inject mocked advService whose getMessage() always returns null until broadcast() runs
  // inject real (or spy) tronNetDelegate wrapping a real Manager with an in-memory account funded
  // for exactly one transfer amount

  ExecutorService pool = Executors.newFixedThreadPool(2);
  CountDownLatch startLatch = new CountDownLatch(1);
  Callable<Void> task = () -> {
      startLatch.await();
      handler.handleTransaction(mockPeer, new TransactionMessage(trx.getInstance()));
      return null;
  };
  Future<Void> f1 = pool.submit(task);
  Future<Void> f2 = pool.submit(task);
  startLatch.countDown();
  f1.get();
  f2.get();

  // Expected (per invariant): only ONE entry for trx's id in manager.getPendingTransactions(),
  // and the account balance should reflect exactly one deduction.
  assertEquals(1, countOccurrences(manager.getPendingTransactions(), trx.getTransactionId()));
}
```
Expected (correct) behavior: the second call should throw/absorb a `DupTransactionException` (or be pre-filtered) and the account balance should be debited only once. Current code allows the assertion to fail because `validateDup` and `pendingTransactions.add` provide no protection against an already-pending, not-yet-committed duplicate.

### Citations

**File:** framework/src/main/java/org/tron/core/net/messagehandler/TransactionsMsgHandler.java (L112-120)
```java
      } else {
        try {
          ExecutorServiceManager.submit(
              trxHandlePool, () -> handleTransaction(peer, new TransactionMessage(trx)));
        } catch (RejectedExecutionException e) {
          logger.warn("Submit task to {} failed", trxEsName);
          break;
        }
      }
```

**File:** framework/src/main/java/org/tron/core/net/messagehandler/TransactionsMsgHandler.java (L129-141)
```java
  private void check(PeerConnection peer, TransactionsMessage msg) throws P2pException {
    List<Transaction> list = msg.getTransactions().getTransactionsList();
    Set<Sha256Hash> seen = new HashSet<>(list.size() * 2);
    for (Transaction trx : list) {
      Sha256Hash id = new TransactionMessage(trx).getMessageId();
      if (!seen.add(id)) {
        throw new P2pException(TypeEnum.BAD_MESSAGE,
            "TransactionsMessage contains duplicate transaction: " + id);
      }
      Item item = new Item(id, InventoryType.TRX);
      if (!peer.getAdvInvRequest().containsKey(item)) {
        throw new P2pException(TypeEnum.BAD_MESSAGE,
            "trx: " + msg.getMessageId() + " without request.");
```

**File:** framework/src/main/java/org/tron/core/net/messagehandler/TransactionsMsgHandler.java (L180-187)
```java
    if (advService.getMessage(new Item(trx.getMessageId(), InventoryType.TRX)) != null) {
      return;
    }

    try {
      trx.getTransactionCapsule().checkExpiration(chainBaseManager.getNextBlockSlotTime());
      tronNetDelegate.pushTransaction(trx.getTransactionCapsule());
      advService.broadcast(trx);
```

**File:** framework/src/main/java/org/tron/core/net/TronNetDelegate.java (L325-344)
```java
  public void pushTransaction(TransactionCapsule trx) throws P2pException {
    try {
      trx.setTime(System.currentTimeMillis());
      dbManager.pushTransaction(trx);
    } catch (ContractSizeNotEqualToOneException
        | VMIllegalException e) {
      throw new P2pException(TypeEnum.BAD_TRX, e);
    } catch (ContractValidateException
        | ValidateSignatureException
        | ContractExeException
        | DupTransactionException
        | TaposException
        | TooBigTransactionException
        | TransactionExpirationException
        | ReceiptCheckErrException
        | TooBigTransactionResultException
        | AccountResourceInsufficientException e) {
      throw new P2pException(TypeEnum.TRX_EXE_FAILED, e);
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
