### Title
Unbounded prefix-scan in `DelegatedResourceAccountIndexStore.getV2Index` allows read-amplification DoS via `GetDelegatedResourceAccountIndexV2Servlet` - (File: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java`)

### Summary
`DelegatedResourceAccountIndexStore.getV2Index()` (via `getWithPrefix`) performs a full, unbounded `prefixQuery` scan over every `V2_FROM_PREFIX`/`V2_TO_PREFIX` entry for a given address and deserializes each result, with no cap on the number of entries returned. Because `DelegateResourceProcessor.delegateResource` (and the corresponding `DelegateResourceActuator`) let any funded account create one such index entry per distinct receiver for as little as 1 TRX frozen, an attacker can grow their own index to a large size and then force expensive O(n) scans on every subsequent unauthenticated query to `/wallet/getdelegatedresourceaccountindexv2` (and the gRPC/PBFT/Solidity equivalents).

### Finding Description
`DelegateResourceProcessor.execute` → `delegateResource` writes one `DelegatedResourceAccountIndexCapsule` under `V2_FROM_PREFIX+owner+receiver` and one under `V2_TO_PREFIX+receiver+owner` for every delegation [1](#0-0) . The only validation gate on the number of relationships is a minimum balance check (`delegateBalance must be greater than or equal to 1 TRX`) [2](#0-1)  — there is no cap on the number of distinct receivers/entries an owner can accumulate.

On the read side, `DelegatedResourceAccountIndexStore.getV2Index` calls `getWithPrefix`, which runs two `prefixQuery` calls and materializes/sorts/deserializes *every* matching entry with no limit parameter [3](#0-2) . `prefixQuery` itself walks the underlying LevelDB/RocksDB iterator from the seek key until the prefix no longer matches, collecting all matches into a `HashMap` [4](#0-3) [5](#0-4) , and `TronStoreWithRevoking.prefixQuery` additionally reflectively constructs a capsule object for every returned key [6](#0-5) . The Chainbase-level implementation further merges root and snapshot layers per call [7](#0-6) , so the scan cost scales with index size on every layer that must be merged.

This is directly reachable by an unauthenticated client through `GetDelegatedResourceAccountIndexV2Servlet.fillResponse` → `Wallet.getDelegatedResourceAccountIndexV2` → `DelegatedResourceAccountIndexStore.getV2Index` [8](#0-7) [9](#0-8) , and equivalently via gRPC (`RpcApiService.getDelegatedResourceAccountIndexV2`) and the Solidity/PBFT mirrors of the same servlet.

The only guard in front of this endpoint is `RateLimiterServlet`, which limits requests-per-second (default 1000 qps per-endpoint, 10000 qps per-IP, 50000 qps global) but does not account for the *cost* of each individual request [10](#0-9) . A rate limiter that caps requests/sec does nothing to bound the O(index-size) work performed per accepted request, so it does not close this gap.

### Impact Explanation
This maps to a "DoS via RPC-API" impact class: once an attacker has inflated their own delegation index, every subsequent (unauthenticated, third-party or attacker-driven) query against that address forces the serving node to perform a linear scan and per-entry deserialization proportional to the attacker-chosen size, rather than a bounded/paginated cost. Sustained concurrent querying (up to the default per-endpoint/IP/global QPS ceilings) against a bloated index can materially increase CPU and I/O load on FullNode/SolidityNode/PBFT nodes serving this API, degrading availability for other RPC clients. The impact is scoped to read-side resource exhaustion; it does not affect consensus, funds, or keys.

### Likelihood Explanation
Preconditions and cost are real but not prohibitive: the attacker needs an ordinary funded account, at least 1 TRX frozen as V2 bandwidth/energy per relationship, and one existing, non-contract receiver account per relationship (`DelegateResourceProcessor.validate` rejects self-delegation, non-existent receivers, and contract receivers) [11](#0-10) . Building a maximal index (e.g., 100,000 entries) requires locking a correspondingly large amount of TRX (opportunity cost, not spent) and either reusing/creating that many receiver accounts, which is a non-trivial but realistically attainable investment for a moderately resourced attacker, and each `DelegateResourceContract`/native call is otherwise cheap (bandwidth/energy only, no meaningful protocol fee gating repeated delegation creation). Once built, the read amplification is fully repeatable and free per query beyond the standard rate limits, and requires no privileged role, key leak, or non-default configuration.

### Recommendation
- Add a hard cap on the number of distinct delegation relationships (`V2_FROM_PREFIX`/`V2_TO_PREFIX` entries) an account may hold, enforced in `DelegateResourceProcessor.validate`/`DelegateResourceActuator.validate`.
- Add pagination/limit parameters to `getV2Index`/`getWithPrefix`/`prefixQuery` (mirroring the existing `getNext(key, limit)` API already present in `IRevokingDB`) and expose a paged `GetDelegatedResourceAccountIndexV2` API instead of returning the unbounded full list.
- Consider a dedicated, stricter rate limit or cost-based throttling for `GetDelegatedResourceAccountIndexV2Servlet` given its data-dependent cost profile.

### Proof of Concept
```java
// Benchmark-style JUnit demonstrating non-bounded response cost
@Test
public void testGetV2IndexScalesWithEntryCount() {
  byte[] smallOwner = "smallOwner".getBytes();
  byte[] bigOwner = "bigOwner".getBytes();

  for (int i = 0; i < 10; i++) {
    delegatedResourceAccountIndexStore.delegateV2(smallOwner, ("recv" + i).getBytes(), i);
  }
  for (int i = 0; i < 100_000; i++) {
    delegatedResourceAccountIndexStore.delegateV2(bigOwner, ("recv" + i).getBytes(), i);
  }

  long t0 = System.nanoTime();
  delegatedResourceAccountIndexStore.getV2Index(smallOwner);
  long smallLatency = System.nanoTime() - t0;

  long t1 = System.nanoTime();
  delegatedResourceAccountIndexStore.getV2Index(bigOwner);
  long bigLatency = System.nanoTime() - t1;

  // Demonstrates unbounded, non-constant cost proportional to attacker-controlled index size
  Assert.assertTrue(bigLatency > smallLatency * 100);
}
```
At the request level: repeated unauthenticated `GET /wallet/getdelegatedresourceaccountindexv2?value=<attacker_address>` calls against a node holding the 100,000-entry index will show measurably higher latency and node CPU/I/O than equivalent calls against a normal (small) index, while both are accepted under the same default per-endpoint QPS limit.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L52-55)
```java
    long delegateBalance = param.getDelegateBalance();
    if (delegateBalance < TRX_PRECISION) {
      throw new ContractValidateException("delegateBalance must be greater than or equal to 1 TRX");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L95-114)
```java
    byte[] receiverAddress = param.getReceiverAddress();

    if (!DecodeUtil.addressValid(receiverAddress)) {
      throw new ContractValidateException("Invalid receiverAddress");
    }
    if (Arrays.equals(receiverAddress, ownerAddress)) {
      throw new ContractValidateException(
          "receiverAddress must not be the same as ownerAddress");
    }
    AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
    if (receiverCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(receiverAddress);
      throw new ContractValidateException(
          ActuatorConstant.ACCOUNT_EXCEPTION_STR
              + readableOwnerAddress + NOT_EXIST_STR);
    }
    if (receiverCapsule.getType() == Protocol.AccountType.Contract) {
      throw new ContractValidateException(
          "Do not allow delegate resources to contract addresses");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L166-180)
```java
    //modify DelegatedResourceAccountIndex
    long now = repo.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    byte[] fromKey = Bytes.concat(
        DelegatedResourceAccountIndexStore.getV2_FROM_PREFIX(), ownerAddress, receiverAddress);
    DelegatedResourceAccountIndexCapsule toIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(receiverAddress));
    toIndexCapsule.setTimestamp(now);
    repo.updateDelegatedResourceAccountIndex(fromKey, toIndexCapsule);

    byte[] toKey = Bytes.concat(
        DelegatedResourceAccountIndexStore.getV2_TO_PREFIX(), receiverAddress, ownerAddress);
    DelegatedResourceAccountIndexCapsule fromIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(ownerAddress));
    fromIndexCapsule.setTimestamp(now);
    repo.updateDelegatedResourceAccountIndex(toKey, fromIndexCapsule);
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L114-138)
```java
  public DelegatedResourceAccountIndexCapsule getV2Index(byte[] address) {
    return getWithPrefix(V2_FROM_PREFIX, V2_TO_PREFIX, address);
  }

  private DelegatedResourceAccountIndexCapsule getWithPrefix(byte[] fromPrefix, byte[] toPrefix, byte[] address) {
    DelegatedResourceAccountIndexCapsule tmpIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(address));

    byte[] key = Bytes.concat(fromPrefix, address);
    List<DelegatedResourceAccountIndexCapsule> tmpToList =
        new ArrayList<>(this.prefixQuery(key).values());
    tmpToList.sort(Comparator.comparing(DelegatedResourceAccountIndexCapsule::getTimestamp));
    List<ByteString> list = tmpToList.stream()
        .map(DelegatedResourceAccountIndexCapsule::getAccount).collect(Collectors.toList());
    tmpIndexCapsule.setAllToAccounts(list);

    key = Bytes.concat(toPrefix, address);
    List<DelegatedResourceAccountIndexCapsule> tmpFromList =
        new ArrayList<>(this.prefixQuery(key).values());
    tmpFromList.sort(Comparator.comparing(DelegatedResourceAccountIndexCapsule::getTimestamp));
    list = tmpFromList.stream().map(DelegatedResourceAccountIndexCapsule::getAccount).collect(
        Collectors.toList());
    tmpIndexCapsule.setAllFromAccounts(list);
    return tmpIndexCapsule;
  }
```

**File:** chainbase/src/main/java/org/tron/common/storage/leveldb/LevelDbDataSourceImpl.java (L365-384)
```java
  @Override
  public Map<WrappedByteArray, byte[]> prefixQuery(byte[] key) {
    resetDbLock.readLock().lock();
    try (DBIterator iterator = getDBIterator()) {
      Map<WrappedByteArray, byte[]> result = new HashMap<>();
      for (iterator.seek(key); iterator.hasNext(); iterator.next()) {
        Entry<byte[], byte[]> entry = iterator.peekNext();
        if (Bytes.indexOf(entry.getKey(), key) == 0) {
          result.put(WrappedByteArray.of(entry.getKey()), entry.getValue());
        } else {
          return result;
        }
      }
      return result;
    } catch (IOException e) {
      throw new RuntimeException(e);
    } finally {
      resetDbLock.readLock().unlock();
    }
  }
```

**File:** chainbase/src/main/java/org/tron/common/storage/rocksdb/RocksDbDataSourceImpl.java (L380-397)
```java
  @Override
  public Map<WrappedByteArray, byte[]> prefixQuery(byte[] key) {
    resetDbLock.readLock().lock();
    try (final ReadOptions readOptions = getReadOptions();
         final RocksIterator iterator = getRocksIterator(readOptions)) {
      Map<WrappedByteArray, byte[]> result = new HashMap<>();
      for (iterator.seek(key); iterator.isValid(); iterator.next()) {
        if (Bytes.indexOf(iterator.key(), key) == 0) {
          result.put(WrappedByteArray.of(iterator.key()), iterator.value());
        } else {
          return result;
        }
      }
      return result;
    } finally {
      resetDbLock.readLock().unlock();
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java (L203-213)
```java
  public Map<WrappedByteArray, T> prefixQuery(byte[] key) {
    return revokingDB.prefixQuery(key).entrySet().stream().collect(
        Collectors.toMap(Map.Entry::getKey, e -> {
          try {
            return of(e.getValue());
          } catch (BadItemException e1) {
            throw new RuntimeException(e1);
          }
        }
    ));
  }
```

**File:** chainbase/src/main/java/org/tron/core/db2/core/Chainbase.java (L352-368)
```java
  public Map<WrappedByteArray, byte[]> prefixQuery(byte[] key) {
    Map<WrappedByteArray, byte[]> result = prefixQueryRoot(key);
    Map<WrappedByteArray, byte[]>  snapshot = prefixQuerySnapshot(key);
    result.putAll(snapshot);
    result.entrySet().removeIf(e -> e.getValue() == null);
    return result;
  }

  private Map<WrappedByteArray, byte[]> prefixQueryRoot(byte[] key) {
    Map<WrappedByteArray, byte[]> result = new HashMap<>();
    if (((SnapshotRoot) head.getRoot()).db.getClass() == LevelDB.class) {
      result = ((LevelDB) ((SnapshotRoot) head.getRoot()).db).getDb().prefixQuery(key);
    } else if (((SnapshotRoot) head.getRoot()).db.getClass() == RocksDB.class) {
      result = ((RocksDB) ((SnapshotRoot) head.getRoot()).db).getDb().prefixQuery(key);
    }
    return result;
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java (L60-68)
```java
  private void fillResponse(ByteString address, boolean visible, HttpServletResponse response)
      throws IOException {
    DelegatedResourceAccountIndex reply =
        wallet.getDelegatedResourceAccountIndexV2(address);
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1053-1064)
```java
  public DelegatedResourceAccountIndex getDelegatedResourceAccountIndexV2(ByteString address) {
    if (address == null || address.size() != DecodeUtil.ADDRESS_SIZE / 2) {
      return DelegatedResourceAccountIndex.getDefaultInstance();
    }
    DelegatedResourceAccountIndexCapsule accountIndexCapsule = chainBaseManager
        .getDelegatedResourceAccountIndexStore().getV2Index(address.toByteArray());
    if (accountIndexCapsule != null) {
      return accountIndexCapsule.getInstance();
    } else {
      return DelegatedResourceAccountIndex.getDefaultInstance();
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java (L103-136)
```java
  @Override
  protected void service(HttpServletRequest req, HttpServletResponse resp)
      throws ServletException, IOException {

    RuntimeData runtimeData = new RuntimeData(req);
    IRateLimiter rateLimiter = container.get(KEY_PREFIX_HTTP, getClass().getSimpleName());

    // Check per-endpoint first to avoid consuming global IP/QPS quota for requests
    // that would be rejected by the per-endpoint limiter anyway. acquirePermit()
    // chooses blocking or non-blocking semantics based on rate.limiter.apiNonBlocking.
    boolean perEndpointAcquired = rateLimiter == null || rateLimiter.acquirePermit(runtimeData);
    boolean acquireResource = perEndpointAcquired && GlobalRateLimiter.acquirePermit(runtimeData);

    String contextPath = req.getContextPath();
    String url = Strings.isNullOrEmpty(req.getServletPath())
        ? MetricLabels.UNDEFINED : contextPath + req.getServletPath();
    // int64_as_string is honored only on GET requests (URL query). POST is intentionally
    // unsupported because reading the body here would consume request.getReader() and
    // break downstream servlets that read it themselves.
    if ("GET".equalsIgnoreCase(req.getMethod())) {
      JsonFormat.setInt64AsString(Util.getInt64AsString(req));
    }
    try {
      resp.setContentType("application/json; charset=utf-8");

      if (acquireResource) {
        Histogram.Timer requestTimer = Metrics.histogramStartTimer(
            MetricKeys.Histogram.HTTP_SERVICE_LATENCY, url);
        super.service(req, resp);
        Metrics.histogramObserve(requestTimer);
      } else {
        resp.getWriter()
            .println(Util.printErrorMsg(new IllegalAccessException("lack of computing resources")));
      }
```
