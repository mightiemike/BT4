### Title
Unsynchronized `SnapshotManager.merge()` races with `synchronized` `flush()`/`createCheckpoint()`/`refreshOne()`, causing snapshot-chain divergence - ([File: chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java])

### Summary
`SnapshotManager.merge()` is the only session-lifecycle method (compared to `commit()`, `revoke()`, `pop()`, `buildSession()`) that is **not** declared `synchronized`, even though it mutates the same shared linked-list of `Snapshot`/`Chainbase` heads and `size`/`activeSession` state that `flush()` → `createCheckpoint()` → `refresh()`/`refreshOne()` walk while holding the object monitor. Because `Manager.pushTransaction()` calls `tmpSession.merge()` inside `synchronized(this)` (on `Manager`, not `SnapshotManager`) and `Manager.generateBlock()` calls `tmpSession.merge()` with no matching lock at all, an unprivileged attacker's broadcast transaction processed via `pushTransaction()` can race with the witness's concurrent `generateBlock()` block-production path, both hitting the shared `SnapshotManager` singleton without a common mutual-exclusion guard around `merge()`.

### Finding Description
`buildSession()`, `commit()`, `revoke()`, and `pop()` are all `synchronized void`, meaning that while `flush()` (called from inside `buildSession()`'s synchronized block) is running `createCheckpoint()` (which walks `head.getRoot()` → `next.getNext()` for `flushCount` steps per `Chainbase` and batches key/values into `checkTmpStore`/`CheckPointV2Store`) and then `refresh()`/`refreshOne()` (which independently re-walks the same chain from `root` for `flushCount` steps and calls `root.merge(snapshots)` then repoints `db.setHead(...)`), no other thread can enter `buildSession()`, `commit()`, `revoke()`, or `pop()` concurrently: [1](#0-0) [2](#0-1) [3](#0-2) 

However, `merge()` itself has **no `synchronized` modifier**, unlike its siblings: [4](#0-3) 

`merge()` mutates `db.getHead()` (via `getPrevious().merge(db.getHead())`), calls `retreat()` which changes every `Chainbase`'s head pointer and decrements `size`, and decrements `activeSession` — the exact same head-chain fields that `refreshOne()` assumes are stable while it counts `flushCount` hops from `root` and re-links `next.getNext().setPrevious(root)` / `root.setNext(next.getNext())`.

`ISession.merge()` is reachable via two independent, non-mutually-exclusive call sites in `Manager`:
- `pushTransaction()` (public broadcast-transaction entrypoint, unprivileged): `synchronized(this)` around `buildSession()`+`processTransaction()`+`tmpSession.merge()` — synchronizes on the `Manager` monitor, not `SnapshotManager`'s. [5](#0-4) 
- `generateBlock()` (witness's periodic block-production loop), which calls `revokingStore.buildSession()` / `tmpSession.merge()` per packed transaction with **no** `synchronized` wrapper on `Manager` at all. [6](#0-5) 

Because these two call sites use different (or no) locks on `Manager`, and `SnapshotManager.merge()` itself takes no lock on the `SnapshotManager` instance, a broadcast transaction processed by `pushTransaction()` can execute `SnapshotManager.merge()` concurrently with the witness thread's `buildSession()` → `flush()` → `createCheckpoint()`/`refreshOne()` sequence (or with another `merge()`/`commit()`/`revoke()` call in flight from `generateBlock()`). `flushCount` is only reset to `0` at the very end of `flush()` [7](#0-6) , and it, plus `size`/head-chain pointers, can be concurrently perturbed by the unsynchronized `merge()` mutating `retreat()`'s `size`/head list while `createCheckpoint()` and `refreshOne()` are independently iterating that same chain by count — exactly the "iterate flushCount independently" scenario described in the question. This can produce a checkpoint batch (`checkTmpStore`) that reflects a different snapshot boundary than what `refreshOne()` actually merges into `SnapshotRoot`, or corrupt the linked list (`next.getNext()` becoming stale/null) resulting in `NullPointerException`/`IndexOutOfBoundsException` or a silently wrong merged state.

### Impact Explanation
If two honest nodes experience different thread-interleavings between their local mempool/broadcast-transaction processing and their own block-generation timing (which is inherently non-deterministic and driven by wall-clock/OS scheduling, not consensus), each node's `SnapshotManager` could commit a different checkpoint/merge boundary for the same logical sequence of transactions. This is a state-divergence / chain-split-class issue (differing recovered `AccountStore` contents after crash-recovery from `checkTmpStore`/`CheckPointV2Store`), or, more likely given actual code paths, an unhandled runtime exception in `refreshOne()` (`hitDown = true`, node halt via `TronError(DB_FLUSH)`), i.e., a denial-of-service on the affected node rather than a silent value theft.

### Likelihood Explanation
Exploitability depends on precise timing since `merge()`'s window is short and `buildSession()`/`flush()`/`refresh()` mostly execute inside the synchronized region entered from the same thread producing the block. An attacker cannot directly control the witness's block-production thread, but can flood `pushTransaction()` broadcast calls to increase the probability of overlapping execution windows with `generateBlock()`, since `generateBlock()` does not synchronize with `pushTransaction()` on the same monitor. This makes it a genuine, but timing-dependent (non-guaranteed), concurrency defect — not a directly attacker-forced deterministic divergence.

### Recommendation
Make `SnapshotManager.merge()` `synchronized` (matching `commit()`, `revoke()`, `pop()`, `buildSession()`), and audit `Manager.generateBlock()` to ensure it acquires the same mutual-exclusion guard (`transactionLock`/`this`) that `pushTransaction()` uses around `revokingStore.buildSession()`/`tmpSession.merge()`, so `flush()`'s `createCheckpoint()`+`refresh()` sequence can never interleave with an in-flight `merge()`.

### Proof of Concept
```java
// Integration test sketch for SnapshotManagerTest
@Test
public void testMergeRaceWithFlush() throws Exception {
  revokingDatabase.setMaxFlushCount(1);
  revokingDatabase.setUnChecked(false);
  revokingDatabase.setMaxSize(2); // small to force flush() quickly

  ExecutorService pool = Executors.newFixedThreadPool(2);
  CountDownLatch start = new CountDownLatch(1);

  Runnable txWriter = () -> {
    try {
      start.await();
      for (int i = 0; i < 500; i++) {
        try (ISession s = revokingDatabase.buildSession()) {
          tronDatabase.put(("k" + i).getBytes(), new ProtoCapsuleTest(("v" + i).getBytes()));
          s.merge(); // unsynchronized path
        }
      }
    } catch (Exception ignored) {}
  };

  Future<?> f1 = pool.submit(txWriter);
  Future<?> f2 = pool.submit(txWriter);
  start.countDown();
  f1.get(); f2.get();

  // Assert no corruption/exception occurred, and checkTmpStore batch
  // matches exactly what refreshOne() merged into SnapshotRoot.
  Assert.assertFalse(revokingDatabase.hitDown); // no TronError(DB_FLUSH)
  // Compare recovered AccountStore state hash after check()/recover()
  // against the direct in-memory merged root state hash.
}
```
Expected (if fixed): no exceptions, deterministic final state regardless of interleaving. Expected (if vulnerable): intermittent `NullPointerException`/`IndexOutOfBoundsException` in `refreshOne()`, or divergent final key/value contents between repeated runs with different thread scheduling.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java (L170-205)
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

**File:** chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java (L303-326)
```java
  private void refreshOne(Chainbase db) {
    if (Snapshot.isRoot(db.getHead())) {
      return;
    }

    List<Snapshot> snapshots = new ArrayList<>();

    SnapshotRoot root = (SnapshotRoot) db.getHead().getRoot();
    Snapshot next = root;
    for (int i = 0; i < flushCount; ++i) {
      next = next.getNext();
      snapshots.add(next);
    }

    root.merge(snapshots);

    root.resetSolidity();
    if (db.getHead() == next) {
      db.setHead(root);
    } else {
      next.getNext().setPrevious(root);
      root.setNext(next.getNext());
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java (L328-355)
```java
  public void flush() {
    if (unChecked) {
      return;
    }

    if (shouldBeRefreshed()) {
      try {
        long start = System.currentTimeMillis();
        if (!isV2Open()) {
          deleteCheckpoint();
        }
        createCheckpoint();

        long checkPointEnd = System.currentTimeMillis();
        refresh();
        flushCount = 0;
        logger.info("Flush cost: {} ms, create checkpoint cost: {} ms, refresh cost: {} ms.",
            System.currentTimeMillis() - start,
            checkPointEnd - start,
            System.currentTimeMillis() - checkPointEnd
        );
      } catch (TronDBException e) {
        logger.error(" Find fatal error, program will be exited soon.", e);
        hitDown = true;
        throw new TronError(e, TronError.ErrCode.DB_FLUSH);
      }
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java (L357-405)
```java
  public void createCheckpoint() {
    TronDatabase<byte[]> checkPointStore = null;
    try {
      Map<WrappedByteArray, WrappedByteArray> batch = new HashMap<>();
      for (Chainbase db : dbs) {
        Snapshot head = db.getHead();
        if (Snapshot.isRoot(head)) {
          return;
        }

        String dbName = db.getDbName();

        if (Objects.equals(dbName, "trans-cache")) {
          // trans-cache is deprecated
          continue;
        }

        Snapshot next = head.getRoot();
        for (int i = 0; i < flushCount; ++i) {
          next = next.getNext();
          SnapshotImpl snapshot = (SnapshotImpl) next;
          DB<Key, Value> keyValueDB = snapshot.getDb();
          for (Map.Entry<Key, Value> e : keyValueDB) {
            Key k = e.getKey();
            Value v = e.getValue();
            batch.put(WrappedByteArray.of(Bytes.concat(simpleEncode(dbName), k.getBytes())),
                WrappedByteArray.of(v.encode()));
          }
        }
      }
      if (isV2Open()) {
        String dbName = String.valueOf(System.currentTimeMillis());
        checkPointStore = getCheckpointDB(dbName);
      } else {
        checkPointStore = checkTmpStore;
      }

      checkPointStore.updateByBatch(batch.entrySet().stream()
              .map(e -> Maps.immutableEntry(e.getKey().getBytes(), e.getValue().getBytes()))
              .collect(HashMap::new, (m, k) -> m.put(k.getKey(), k.getValue()), HashMap::putAll));

    } catch (Exception e) {
      throw new TronDBException(e);
    } finally {
      if (isV2Open() && checkPointStore != null) {
        checkPointStore.close();
      }
    }
  }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L924-940)
```java
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
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1740-1745)
```java
      try (ISession tmpSession = revokingStore.buildSession()) {
        accountStateCallBack.preExeTrans();
        processTransaction(trx, blockCapsule);
        accountStateCallBack.exeTransFinish();
        tmpSession.merge();
        toBePacked.add(trx);
```
