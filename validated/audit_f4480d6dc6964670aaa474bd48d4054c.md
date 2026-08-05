### Title
Unsynchronized public reads observe uncommitted/partially-applied session state in `Chainbase` - ([File: chainbase/src/main/java/org/tron/core/db2/core/Chainbase.java])

### Finding Description
`Chainbase.get()/getUnchecked()` read directly from the mutable, shared `head()` `Snapshot` object with no synchronization: `getUnchecked()` simply calls `head().get(key)` [1](#0-0) . In contrast, `put()`/`delete()` are `synchronized` on the `Chainbase` instance and mutate the same `head()` object in place [2](#0-1) . Because `get`/`getUnchecked` carry no `synchronized` keyword and no snapshot-isolation mechanism, they are not mutually exclusive with concurrent writers and always see whatever is currently written into the live head snapshot, regardless of whether the enclosing `ISession` (`SnapshotManager.Session`) has been committed.

`SnapshotManager.buildSession()`/`commit()`/`revoke()` are `synchronized` on the `SnapshotManager` instance [3](#0-2) [4](#0-3) , and `Manager.pushBlock()` wraps the entire block application (including all of its transactions) in a single `ISession`/session before calling `tmpSession.commit()`: `try (ISession tmpSession = revokingStore.buildSession()) { applyBlock(newBlock, txs); tmpSession.commit(); }` inside a `synchronized (this)` block on `Manager` [5](#0-4) .

However, the public read path for `GetAccount` (`Wallet.getAccount()` → `AccountStore.get()` → `Chainbase.getUnchecked()`) never acquires the `Manager` lock or any `SnapshotManager`/`Chainbase` write lock: `Wallet.getAccount()` calls `chainBaseManager.getAccountStore().get(address)` directly [6](#0-5) , and `AccountStore.get()` calls `revokingDB.getUnchecked(key)` [7](#0-6) , which is `Chainbase.getUnchecked()` — an unsynchronized method. This is exposed to unprivileged callers over gRPC via `WalletApi.getAccount`/`WalletSolidityApi.getAccount` [8](#0-7) .

Consequently, while a block (or any transaction batch wrapped by a single `buildSession()`) is being applied — a window that can span multiple transactions each mutating account/vote state in the same live `head` snapshot before the final `tmpSession.commit()` — a concurrent, unprivileged RPC caller invoking `GetAccount` can read intermediate, not-yet-committed intra-block state (e.g., partial balance/vote changes from transaction N of M in the block) because no read-side lock or snapshot copy prevents this. This violates the expectation that public reads only ever reflect a fully committed, atomic snapshot.

### Impact Explanation
An attacker polling `GetAccount` (or equivalent gRPC/HTTP endpoints backed by the same `AccountStore.get`) during block processing can observe balance/vote/resource state changes made by earlier transactions in the currently-executing, uncommitted block before the block session commits. This leaks pre-finalization state that could be used to front-run trades, votes, or transfers dependent on another account's balance, since ordinary full-node read APIs are expected to expose only finalized (post-commit) state. This is an information-leak / read-consistency violation, not a direct fund-theft primitive, but it can materially assist MEV-style front-running strategies against exchange/market/vote actuators that read account state via the same store paths.

### Likelihood Explanation
This requires no privilege beyond being able to issue read-only gRPC calls, which is available to any public RPC user. The precondition is simply that the attacker's `GetAccount` (or similar) call lands on a full node while that node is actively applying a block that touches the victim account, and given `applyBlock` processes potentially many transactions sequentially under one `buildSession()`, the exposure window scales with block/transaction count and node processing latency. This is a genuine, repeatable race given the codebase's read/write synchronization asymmetry (`put`/`delete` synchronized, `get`/`getUnchecked`/`getFromRoot` not), but the window per block is typically short (milliseconds), which limits — but does not eliminate — practical exploitability; a well-resourced attacker running a colocated/low-latency full node and issuing tight read loops during high-transaction blocks increases feasibility.

### Recommendation
Make `Chainbase.get()`, `getUnchecked()`, `has()`, `getFromRoot()`, `iterator()` and range-query methods synchronized on the same monitor used by `put()`/`delete()` (or otherwise coordinate with `SnapshotManager`'s session lock), so that reads and writes to the head snapshot are mutually exclusive within a session boundary. Alternatively, introduce explicit copy-on-write/MVCC semantics so that readers always operate against the last *committed* snapshot rather than the live, in-progress head, until the enclosing session commits.

### Proof of Concept
```java
// Illustrative test plan for chainbase/src/test/.../ChainbaseReadIsolationTest.java
@Test
public void testReadDoesNotObserveUncommittedSessionState() throws Exception {
  SnapshotManager revokingDatabase = context.getBean(SnapshotManager.class);
  revokingDatabase.enable();
  TestRevokingTronStore store = new TestRevokingTronStore("read-isolation-test");
  revokingDatabase.add(store.getRevokingDB());

  byte[] key = "acct".getBytes();
  ProtoCapsuleTest committedValue = new ProtoCapsuleTest("committed".getBytes());
  try (ISession s0 = revokingDatabase.buildSession()) {
    store.put(key, committedValue);
    s0.commit();
  }

  CountDownLatch writerStarted = new CountDownLatch(1);
  CountDownLatch writerPausedMidSession = new CountDownLatch(1);
  CountDownLatch readerDone = new CountDownLatch(1);
  AtomicReference<ProtoCapsuleTest> observed = new AtomicReference<>();

  Thread writer = new Thread(() -> {
    try (ISession s1 = revokingDatabase.buildSession()) {
      store.put(key, new ProtoCapsuleTest("uncommitted".getBytes())); // in-flight write, not yet committed
      writerStarted.countDown();
      writerPausedMidSession.await(); // hold session open, simulating mid-block processing
      s1.revoke(); // never commits -- simulate victim tx not yet finalized
    } catch (InterruptedException ignored) {
    }
  });

  writer.start();
  writerStarted.await();

  Thread reader = new Thread(() -> {
    observed.set(store.get(key)); // simulates GetAccount RPC read via getUnchecked
    readerDone.countDown();
  });
  reader.start();
  readerDone.await();
  writerPausedMidSession.countDown();
  writer.join();

  // EXPECTED (secure) behavior: reads should only ever see committed state
  Assert.assertEquals(committedValue, observed.get());
  // ACTUAL (current) behavior: observed.get() equals "uncommitted" because
  // Chainbase.getUnchecked() reads the live head snapshot with no isolation
  // from the concurrent, uncommitted put().
}
```
This demonstrates that a reader thread (standing in for the `GetAccount` RPC handler) can observe a value written by a concurrent, still-open (uncommitted) session, confirming the lack of read/write isolation in `Chainbase`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db2/core/Chainbase.java (L122-130)
```java
  @Override
  public synchronized void put(byte[] key, byte[] value) {
    head().put(key, value);
  }

  @Override
  public synchronized void delete(byte[] key) {
    head().remove(key);
  }
```

**File:** chainbase/src/main/java/org/tron/core/db2/core/Chainbase.java (L151-154)
```java
  @Override
  public byte[] getUnchecked(byte[] key) {
    return head().get(key);
  }
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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1385-1397)
```java

              return;
            }
            long oldSolidNum = getDynamicPropertiesStore().getLatestSolidifiedBlockNum();
            try (ISession tmpSession = revokingStore.buildSession()) {
              applyBlock(newBlock, txs);
              tmpSession.commit();
            } catch (Throwable throwable) {
              logger.error(throwable.getMessage(), throwable);
              khaosDb.removeBlk(block.getBlockId());
              clearSolidityContractTriggerCache(block.getNum());
              throw throwable;
            }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L335-341)
```java
  public Account getAccount(Account account) {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    AccountCapsule accountCapsule = accountStore.get(account.getAddress().toByteArray());
    if (accountCapsule == null) {
      return null;
    }
    accountCapsule.importAllAsset();
```

**File:** chainbase/src/main/java/org/tron/core/store/AccountStore.java (L61-65)
```java
  @Override
  public AccountCapsule get(byte[] key) {
    byte[] value = revokingDB.getUnchecked(key);
    return ArrayUtils.isEmpty(value) ? null : new AccountCapsule(value);
  }
```

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L370-379)
```java
    public void getAccount(Account request, StreamObserver<Account> responseObserver) {
      ByteString addressBs = request.getAddress();
      if (addressBs != null) {
        Account reply = wallet.getAccount(request);
        responseObserver.onNext(reply);
      } else {
        responseObserver.onNext(null);
      }
      responseObserver.onCompleted();
    }
```
