### Title
Race between unsynchronized `SnapshotManager.merge()` and synchronized `revoke()`/`buildSession()` allows a concurrently-firing `session.reset()` to silently no-op a transaction's snapshot merge - ([File: chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java])

### Summary
`SnapshotManager.merge()` (line 170) is not `synchronized`, unlike its sibling state-mutating methods `buildSession()` (119), `revoke()` (184) and `commit()` (207), which all are. This asymmetry lets a concurrent thread (e.g. block production via `Manager.generateBlock`, which calls `session.reset()` without holding `Manager`'s intrinsic lock that `pushTransaction` uses) mutate `size`/`dbs` via `revoke()` in between `tmpSession`'s `buildSession()` and its `merge()` call inside `Manager.pushTransaction`.

### Finding Description
In `Manager.pushTransaction` (framework/src/main/java/org/tron/core/db/Manager.java:924-944), the outer persistent `session` and inner `tmpSession` snapshots are built and merged entirely inside a `synchronized (this)` block: [1](#0-0) 

This code assumes that the `size`/`activeSession` bookkeeping in `SnapshotManager` stays consistent for the duration of `processTransaction(...)` + `tmpSession.merge()`. However, that consistency is only guaranteed if every mutator of `SnapshotManager` state acquires the same lock. Looking at `SnapshotManager`:
- `buildSession(boolean)` is `synchronized` and calls `advance()` (`size++`). [2](#0-1) 
- `revoke()` is `synchronized` and calls `retreat()` (`size--`). [3](#0-2) 
- `commit()` is `synchronized`. [4](#0-3) 
- `merge()`, in contrast, is **not** `synchronized`, and reads/mutates the shared `size` field and the `dbs` snapshot chain without any lock: [5](#0-4) 

Separately, `Manager.generateBlock` resets the outer `session` (which internally calls `ISession.destroy()` → `SnapshotManager.revoke()`) and rebuilds it, at a point in the method that is not wrapped by the same `synchronized (this)` monitor that `pushTransaction` uses for its critical section: [6](#0-5) 
and again at the end of block generation: [7](#0-6) 

`SessionOptional.reset()`/`valid()`/`setValue()` are individually `synchronized` on the `SessionOptional` singleton, so the *pointer swap* itself is atomic, but that gives no protection to `SnapshotManager.size`, which is what `merge()`'s `size < 2` guard depends on. Because `merge()` takes no lock, a `revoke()` invoked concurrently by `generateBlock`'s `session.reset()` (itself unguarded by `Manager`'s `this` monitor) can race with an in-flight `tmpSession.merge()` inside `pushTransaction`'s otherwise-synchronized block: `revoke()` can decrement `size` from 2 to 1 in the exact window between when `tmpSession`'s `buildSession()` advanced `size` and when `merge()` reads `size` at line 175. This causes `merge()`'s `if (size < 2) return;` no-op branch to trigger, leaving the transaction's writes (balance/fee deduction, resource consumption) stranded in `db.getHead()`, a snapshot that is never folded into the persistent chain and is later discarded when the outer session is torn down/rebuilt by the concurrent `revoke()`/`reset()`.

### Impact Explanation
A transaction that already had its owner's TRX/bandwidth fee deducted and business logic applied in the transient (never-merged) snapshot can vanish from durable state once that snapshot is orphaned and discarded, while the client received a successful `pushTransaction` result. This is a scoped fee/settlement misaccounting: the user is charged in the ephemeral state but the settlement never lands, or (depending on timing) is erased without any refund reconciliation, violating the "one logical spend settles exactly once" invariant for TRX/bandwidth fees.

### Likelihood Explanation
This requires no privileged access — any unprivileged client broadcasting a transaction via the public P2P/API `pushTransaction` path is sufficient. The trigger is a timing race between `Manager.pushTransaction`'s per-tx `tmpSession` lifecycle and a concurrently running `Manager.generateBlock` (or an equivalent internal path resetting the outer `session`) that is not synchronized on the same monitor and calls into non-synchronized `SnapshotManager.merge()`/synchronized `revoke()` on the same underlying `size` counter. Because `merge()` lacks synchronization while `revoke()`/`buildSession()`/`commit()` have it, the race is a genuine, reproducible thread-safety defect rather than a purely theoretical one, though it depends on tight interleaving that a targeted stress/fuzz test can reliably trigger by pinning thread scheduling or adding sleeps at the vulnerable window.

### Recommendation
Make `SnapshotManager.merge()` `synchronized`, consistent with `buildSession()`, `revoke()`, and `commit()`, so that `size`/`activeSession`/`dbs` mutations are serialized against each other. Additionally, audit all call sites that invoke `session.reset()`/`session.setValue()` on the shared `SessionOptional` (e.g. `Manager.generateBlock`) to ensure they acquire the same lock (`Manager`'s `this` monitor, or a dedicated lock protecting `revokingStore` state transitions) that `pushTransaction` relies on for its `session`/`tmpSession` critical section.

### Proof of Concept
Java integration test plan (framework test module):
1. Instrument `SnapshotManager` (or use a spy) so that a countdown latch/hook is inserted right after `tmpSession`'s `buildSession()` returns in `pushTransaction` (i.e., before `processTransaction`/`merge()` executes).
2. From a second thread, call `generateBlock` (or directly invoke `session.reset()`/`revokingStore.revoke()` on the shared instance) to fire during that window.
3. Release the first thread to complete `processTransaction` and call `tmpSession.merge()`.
4. Assert that:
   - The account balance was decremented by exactly the transaction fee (`AccountCapsule.getBalance()` before vs after).
   - The transaction is retrievable from durable store (`chainBaseManager.getTransactionStore().has(txId)`) or, if not, that the balance deduction was rolled back — i.e., exactly one of "accepted+charged" or "rejected+uncharged" holds, never "charged but erased."
5. Run with concurrency stress (loop N iterations with `Thread.sleep`/`CountDownLatch` jitter) to confirm the race reproduces the erasure-after-charge condition, then confirm it disappears after making `merge()` synchronized and properly locking `generateBlock`'s `session.reset()` calls.

### Citations

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L929-940)
```java
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
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1644-1645)
```java
    session.reset();
    session.setValue(revokingStore.buildSession());
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1760-1760)
```java
    session.reset();
```

**File:** chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java (L119-139)
```java
  public synchronized ISession buildSession(boolean forceEnable) {
    if (disabled && !forceEnable) {
      return new Session(this);
    }

    boolean disableOnExit = disabled && forceEnable;
    if (forceEnable) {
      disabled = false;
    }

    if (size > maxSize.get() && !hitDown) {
      flushCount = flushCount + (size - maxSize.get());
      updateSolidity(size - maxSize.get());
      size = maxSize.get();
      flush();
    }

    advance();
    ++activeSession;
    return new Session(this, disableOnExit);
  }
```

**File:** chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java (L170-182)
```java
  public void merge() {
    if (activeSession <= 0) {
      throw new RevokingStoreIllegalStateException(activeSession);
    }

    if (size < 2) {
      return;
    }

    dbs.forEach(db -> db.getHead().getPrevious().merge(db.getHead()));
    retreat();
    --activeSession;
  }
```

**File:** chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java (L184-205)
```java
  public synchronized void revoke() {
    if (disabled) {
      return;
    }

    if (activeSession <= 0) {
      throw new RevokingStoreIllegalStateException(activeSession);
    }

    if (size <= 0) {
      return;
    }

    disabled = true;

    try {
      retreat();
    } finally {
      disabled = false;
    }
    --activeSession;
  }
```

**File:** chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java (L207-219)
```java
  public synchronized void commit() {
    if (activeSession <= 0) {
      throw new RevokingStoreIllegalStateException(activeSession);
    }

    --activeSession;

    dbs.forEach(db -> {
      if (db.getHead().isOptimized()) {
        db.getHead().reloadToMem();
      }
    });
  }
```
