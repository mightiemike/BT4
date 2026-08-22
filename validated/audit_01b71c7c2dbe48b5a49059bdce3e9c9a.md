I have enough evidence now to render a final determination.

Key findings:
- `DelegateResourceContract` requires only `delegateBalance >= TRX_PRECISION` (1 TRX) per delegation, and the receiver only needs to be a valid, existing, non-contract account distinct from the owner [1](#0-0) . An attacker with one funded account can cheaply create many `DelegatedResourceCapsule` entries by delegating 1 TRX each to N distinct pre-existing receiver addresses.
- Each delegation calls `DelegatedResourceAccountIndexStore.delegateV2`, which stores index entries keyed by `V2_FROM_PREFIX/address/receiver` and `V2_TO_PREFIX/address/sender` [2](#0-1) .
- `getV2Index`/`getIndex` call `getWithPrefix`, which performs `prefixQuery` over all entries matching the address prefix, deserializes every matching `DelegatedResourceAccountIndexCapsule`, sorts the full list, and builds an unbounded result list with **no pagination, no limit, and no size cap** [3](#0-2) .
- This is reachable directly by an anonymous RPC/HTTP client via `Wallet.getDelegatedResourceAccountIndex`/`getDelegatedResourceAccountIndexV2` [4](#0-3) , exposed through `RpcApiService.getDelegatedResourceAccountIndex(V2)` [5](#0-4)  and `GetDelegatedResourceAccountIndexV2Servlet` [6](#0-5) . Note by contrast: `getDelegatedResource`/`getDelegatedResourceV2(from,to)` only fetches at most two fixed-key entries and is **not** vulnerable to this unbounded fan-out [7](#0-6) .
- The only mitigation present is a generic per-endpoint/global QPS rate limiter (`RateLimiterServlet`), which limits request *frequency*, not the *cost* of a single request; it does not bound the number of entries returned per query [8](#0-7) . This does not stop a single expensive request from consuming disproportionate CPU/memory once N is large, nor prevent low-QPS but high-per-request-cost floods.

This matches the described vulnerability precisely: unbounded, unpaginated per-address index iteration reachable by any anonymous client at negligible cost (~1 TRX + bandwidth per delegation, reusable receivers can even be other attacker-controlled accounts activated cheaply).

### Title
Unbounded prefix-scan in `getDelegatedResourceAccountIndex(V2)` allows cheap DoS via unpaginated RPC/HTTP listing - (File: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java`)

### Summary
`DelegatedResourceAccountIndexStore.getWithPrefix` performs a full prefix scan and deserializes/sorts every `DelegatedResourceAccountIndexCapsule` entry for a given address with no pagination or size cap. An attacker can cheaply inflate the number of entries for one address by issuing many 1-TRX `DelegateResourceContract` transactions to distinct receivers, then repeatedly query `getDelegatedResourceAccountIndex`/`getDelegatedResourceAccountIndexV2` over gRPC or HTTP to force the full node to do unbounded work per request.

### Finding Description
`DelegateResourceActuator.validate()` enforces only a 1-TRX minimum per delegation and basic receiver-account checks, with no cap on how many distinct receivers an owner may delegate to [1](#0-0) . Each successful delegation adds an entry to `DelegatedResourceAccountIndexStore` via `delegateV2`, prefixed by `V2_FROM_PREFIX`/`V2_TO_PREFIX` plus the address [2](#0-1) .

The read path `getV2Index`/`getIndex` → `getWithPrefix` executes `this.prefixQuery(key)` for both the "to" and "from" directions, materializes all matching values into `ArrayList`s, sorts them by timestamp, and appends every account to the response builder — there is no limit, offset, or truncation logic anywhere in this method [3](#0-2) .

This is directly reachable by an anonymous client: `Wallet.getDelegatedResourceAccountIndex`/`V2` only validates the address size before calling into the store [4](#0-3) , and is exposed unauthenticated via gRPC (`RpcApiService`) and HTTP (`GetDelegatedResourceAccountIndexV2Servlet`) with only generic QPS-based rate limiting, which does not bound per-request result size or work [8](#0-7) . Contrast this with `getPaginatedNowWitnessList`, which explicitly caps `limit` at `WITNESS_COUNT_LIMIT_MAX` [9](#0-8)  — no equivalent cap exists for delegated-resource index queries.

### Impact Explanation
This is a DoS via RPC-API: an attacker inflates the entry count for a chosen address, then issues repeated (or even single very large) queries that force the full node to perform O(N) LevelDB/RocksDB reads, protobuf deserializations, and sorts per request, with no upper bound. At sufficient N this degrades CPU and memory for all clients sharing the node, and can be amplified by parallel requests against the QPS limiter's allowed rate.

### Likelihood Explanation
The precondition is default full-node RPC/HTTP exposure only — no privileged role, no non-default config. Cost to the attacker is proportional to N: N `DelegateResourceContract` transactions of 1 TRX each (recoverable, not burned, since delegation is reversible via `UndelegateResourceContract`) plus each transaction's bandwidth/energy cost, and N target receiver addresses must merely be existing, non-contract accounts (the attacker can activate many cheap accounts). This is inexpensive and fully repeatable, and the flaw is deterministic, not probabilistic, so likelihood is high.

### Recommendation
Add pagination/limit parameters (offset/limit, with a hard maximum like `WITNESS_COUNT_LIMIT_MAX`) to `getWithPrefix`/`getIndex`/`getV2Index` and their `Wallet`/gRPC/HTTP entry points, and/or cap the maximum number of distinct delegation receivers per owner address enforced in `DelegateResourceActuator.validate()`.

### Proof of Concept
1. Fund one owner account and freeze/delegate 1 TRX bandwidth to N (e.g., 50,000) distinct pre-existing receiver addresses via `DelegateResourceContract` transactions (each passes `DelegateResourceActuator.validate()` since `delegateBalance == TRX_PRECISION`).
2. Issue repeated `getDelegatedResourceAccountIndexV2` gRPC/HTTP requests for the owner address.
3. Assert (expected to fail today): response entry count is bounded (e.g., ≤ some MAX_PAGE_SIZE) and handler latency/memory does not scale linearly/unboundedly with N — i.e., `DelegatedResourceAccountIndexStore.getWithPrefix` should never return more than a fixed page size without pagination parameters, contrasted with the current unbounded `prefixQuery` + full-list-build in [3](#0-2) .

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L147-150)
```java
    long delegateBalance = delegateResourceContract.getBalance();
    if (delegateBalance < TRX_PRECISION) {
      throw new ContractValidateException("delegateBalance must be greater than or equal to 1 TRX");
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L77-89)
```java
  public void delegateV2(byte[] from, byte[] to, long time) {
    byte[] fromKey = Bytes.concat(V2_FROM_PREFIX, from, to);
    DelegatedResourceAccountIndexCapsule toIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(to));
    toIndexCapsule.setTimestamp(time);
    this.put(fromKey, toIndexCapsule);

    byte[] toKey = Bytes.concat(V2_TO_PREFIX, to, from);
    DelegatedResourceAccountIndexCapsule fromIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(from));
    fromIndexCapsule.setTimestamp(time);
    this.put(toKey, fromIndexCapsule);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L118-138)
```java
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

**File:** framework/src/main/java/org/tron/core/Wallet.java (L770-777)
```java
  public WitnessList getPaginatedNowWitnessList(long offset, long limit) throws
      MaintenanceUnavailableException {
    if (limit <= 0 || offset < 0) {
      return null;
    }
    if (limit > WITNESS_COUNT_LIMIT_MAX) {
      limit = WITNESS_COUNT_LIMIT_MAX;
    }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L883-913)
```java
  public DelegatedResourceList getDelegatedResource(ByteString fromAddress, ByteString toAddress) {
    DelegatedResourceList.Builder builder = DelegatedResourceList.newBuilder();
    byte[] dbKey = DelegatedResourceCapsule
        .createDbKey(fromAddress.toByteArray(), toAddress.toByteArray());
    DelegatedResourceCapsule delegatedResourceCapsule = chainBaseManager.getDelegatedResourceStore()
        .get(dbKey);
    if (delegatedResourceCapsule != null) {
      builder.addDelegatedResource(delegatedResourceCapsule.getInstance());
    }
    return builder.build();
  }

  public DelegatedResourceList getDelegatedResourceV2(
          ByteString fromAddress, ByteString toAddress) {
    DelegatedResourceList.Builder builder = DelegatedResourceList.newBuilder();
    byte[] dbKey = DelegatedResourceCapsule
        .createDbKeyV2(fromAddress.toByteArray(), toAddress.toByteArray(), false);
    DelegatedResourceCapsule unlockResource = chainBaseManager.getDelegatedResourceStore()
        .get(dbKey);
    if (nonEmptyResource(unlockResource)) {
      builder.addDelegatedResource(unlockResource.getInstance());
    }
    dbKey = DelegatedResourceCapsule
        .createDbKeyV2(fromAddress.toByteArray(), toAddress.toByteArray(), true);
    DelegatedResourceCapsule lockResource = chainBaseManager.getDelegatedResourceStore()
        .get(dbKey);
    if (nonEmptyResource(lockResource)) {
      builder.addDelegatedResource(lockResource.getInstance());
    }
    return builder.build();
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1040-1064)
```java
  public DelegatedResourceAccountIndex getDelegatedResourceAccountIndex(ByteString address) {
    if (address == null || address.size() != DecodeUtil.ADDRESS_SIZE / 2) {
      return DelegatedResourceAccountIndex.getDefaultInstance();
    }
    DelegatedResourceAccountIndexCapsule accountIndexCapsule =
        chainBaseManager.getDelegatedResourceAccountIndexStore().getIndex(address.toByteArray());
    if (accountIndexCapsule != null) {
      return accountIndexCapsule.getInstance();
    } else {
      return DelegatedResourceAccountIndex.getDefaultInstance();
    }
  }

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

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L528-550)
```java
    @Override
    public void getDelegatedResourceAccountIndex(BytesMessage request,
        StreamObserver<org.tron.protos.Protocol.DelegatedResourceAccountIndex> responseObserver) {
      try {
        responseObserver
          .onNext(wallet.getDelegatedResourceAccountIndex(request.getValue()));
      } catch (Exception e) {
        responseObserver.onError(getRunTimeException(e));
      }
      responseObserver.onCompleted();
    }

    @Override
    public void getDelegatedResourceAccountIndexV2(BytesMessage request,
        StreamObserver<org.tron.protos.Protocol.DelegatedResourceAccountIndex> responseObserver) {
      try {
        responseObserver
                .onNext(wallet.getDelegatedResourceAccountIndexV2(request.getValue()));
      } catch (Exception e) {
        responseObserver.onError(getRunTimeException(e));
      }
      responseObserver.onCompleted();
    }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java (L58-67)
```java
  }

  private void fillResponse(ByteString address, boolean visible, HttpServletResponse response)
      throws IOException {
    DelegatedResourceAccountIndex reply =
        wallet.getDelegatedResourceAccountIndexV2(address);
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
```

**File:** framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java (L103-151)
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
    } catch (ServletException | IOException | BadMessageException e) {
      throw e;
    } catch (Exception unexpected) {
      logger.error("Http Api {}, Method:{}. Error：", url, req.getMethod(), unexpected);
    } finally {
      // CRITICAL: this clear pairs with the setInt64AsString call above. Removing it
      // will leak int64_as_string state across requests on reused Tomcat threads,
      // producing intermittent quoted/unquoted output that is very hard to debug.
      JsonFormat.clearInt64AsString();
      // Release whenever the per-endpoint permit was acquired (covers both the normal
      // completion path and the case where GlobalRateLimiter rejected the request).
      if (rateLimiter instanceof IPreemptibleRateLimiter && perEndpointAcquired) {
        ((IPreemptibleRateLimiter) rateLimiter).release();
      }
    }
```
