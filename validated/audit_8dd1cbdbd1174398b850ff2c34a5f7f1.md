### Title
Unbounded prefix-scan + in-memory sort in `getDelegatedResourceAccountIndex(V2)` enables cost-amplified DoS against RPC-API despite request-count rate limiting - ([File: chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java:118-138])

### Summary
`getWithPrefix()` performs two unbounded LevelDB/RocksDB prefix scans and materializes/sorts all matching entries synchronously on the calling HTTP/gRPC thread, with no cap on result size and no query cost accounting. Because `RateLimiterServlet`/`GlobalRateLimiter` limit by request count (QPS/permits) rather than by the size of work a request triggers, an attacker who first inflates one address's delegation index (via many cheap `DelegateResourceContract` calls, since `DelegateResourceActuator.calcFee()` returns `0` and the only cost is `TRX_PRECISION` frozen balance moved, not burned) can subsequently issue a burst of allowed-rate requests that each cost far more CPU/heap than a normal request, degrading service for other users.

### Finding Description
`getWithPrefix()` builds `key = prefix + address` and calls `this.prefixQuery(key)`, converts the returned map's values into an `ArrayList`, and sorts it with `Comparator.comparing(...)`, once for the "to" side and once for the "from" side [1](#0-0) . This is invoked by `getIndex()`/`getV2Index()`, which are reachable from `GetDelegatedResourceAccountIndexServlet` and `RpcApiService.getDelegatedResourceAccountIndexV2` via `Wallet.getDelegatedResourceAccountIndex`/`getDelegatedResourceAccountIndexV2` [2](#0-1) . There is no pagination, no maximum entry count, and no bound tied to computed cost.

The number of entries scanned per query is controlled by the attacker: `DelegateResourceActuator.execute()`/`delegateResource()` inserts one `V2_FROM_PREFIX`/`V2_TO_PREFIX` entry per delegate call via `delegatedResourceAccountIndexStore.delegateV2(...)` [3](#0-2) , and `calcFee()` for this actuator is `0` [4](#0-3) , meaning the only real cost per delegate is bandwidth/energy for the transaction itself (no burn), while `validate()` only requires `delegateBalance >= 1 TRX` of already-frozen balance that is not consumed—`addFrozenBalanceForBandwidthV2(-delegateBalance)`/`addDelegatedFrozenV2BalanceForBandwidth(delegateBalance)` move balance between fields on the same account rather than spending it [5](#0-4) .

On the query side, `RateLimiterServlet.service()` gates requests using `IRateLimiter.acquirePermit()` (QPS/IP-QPS/global preemptible strategies) and a `GlobalRateLimiter`, both of which are strictly request-count/QPS based and carry no notion of the underlying store-scan or sort cost that a specific request will incur [6](#0-5) . Consequently an attacker who has inflated one address's index to a large size can send requests at the servlet's normal allowed rate, but each request costs disproportionately more CPU/heap than requests against ordinary addresses, amplifying resource consumption relative to what the rate limiter is designed to bound.

### Impact Explanation
This matches TRON's "DoS via RPC-API" impact class: an unprivileged funded account can degrade RPC-API responsiveness/availability for other unprivileged clients by driving up handler thread occupancy time and heap usage per request, disproportionate to the rate limiter's per-request cost model. It does not achieve node RCE, key leakage, or ledger/state corruption—the scope is confined to RPC-API availability/performance degradation.

### Likelihood Explanation
Preconditions are default node configuration and only require: (1) an existing funded/activated account able to freeze ≥1 TRX for bandwidth or energy and issue many `DelegateResourceContract` transactions to build up a large from/to index for one address (bounded practically by bandwidth/energy and block throughput, not by any burn-fee), and (2) sending GET/POST requests to `/wallet/getdelegatedresourceaccountindexv2` or the gRPC equivalent for that address, staying within the default per-endpoint QPS/IP-QPS limiter (which is count-based, not cost-based) so requests are not throttled despite their outsized cost. This is fully reproducible with a JUnit/load-test harness directly exercising `DelegatedResourceAccountIndexStore.getWithPrefix()` /`GetDelegatedResourceAccountIndexServlet` against a pre-populated large index, as suggested in the proof idea, though I did not execute such a harness to obtain concrete timing numbers.

### Recommendation
- Cap the number of entries returned/sorted by `getWithPrefix()` (e.g., a hard maximum such as a few thousand entries per direction) and/or add pagination parameters to `getDelegatedResourceAccountIndex`/`V2` APIs.
- Consider persisting per-address to/from lists pre-sorted (e.g., ordered composite keys) so a full re-sort on every read is unnecessary, avoiding O(n log n) work on the request path.
- Make `RateLimiterServlet`/`GlobalRateLimiter` cost-aware for scan-heavy endpoints (e.g., weight permits by estimated result size or elapsed handler time), or add a dedicated tighter limiter specifically for `GetDelegatedResourceAccountIndexServlet`/`GetDelegatedResourceAccountIndexV2Servlet`.
- Consider bounding the number of live delegate relationships an account can create (independent economic cost per relationship) to prevent unbounded index growth.

### Proof of Concept
```java
// JUnit-style PoC (conceptual, to run in framework test module)
@Test
public void testUnboundedPrefixScanCost() {
  byte[] attacker = ByteArray.fromHexString(OWNER_ADDRESS);
  DelegatedResourceAccountIndexStore store =
      dbManager.getChainBaseManager().getDelegatedResourceAccountIndexStore();

  // Step 1: inflate index for `attacker` — each delegateV2 call costs no burn fee
  // (calcFee() == 0), only bandwidth/energy for tx broadcast.
  int N = 50_000;
  for (int i = 0; i < N; i++) {
    byte[] receiver = randomAddress();
    store.delegateV2(attacker, receiver, i);
  }

  // Step 2: measure handler cost of a single query — scales with N
  long start = System.nanoTime();
  DelegatedResourceAccountIndexCapsule result = store.getV2Index(attacker);
  long elapsed = System.nanoTime() - start;

  assertEquals(N, result.getAllToAccountsList().size());
  // elapsed grows ~linearly (scan) + N log N (sort) with attacker-controlled N,
  // demonstrating no cap exists and cost is unbounded relative to a single
  // rate-limiter permit.
}
```
Request-level PoC: repeatedly `GET /wallet/getdelegatedresourceaccountindexv2?value=<attacker_hex_address>` at a rate within `DefaultBaseQqsAdapter`'s configured QPS for `GetDelegatedResourceAccountIndexV2Servlet`, while measuring server-side handler latency/CPU per request against `N` — latency should scale with `N` (attacker-controlled index size) with no server-side cap, confirming the rate limiter does not offset the amplified per-request cost.

### Citations

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

**File:** framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexServlet.java (L58-67)
```java
  private void fillResponse(ByteString address, boolean visible, HttpServletResponse response)
      throws IOException {
    DelegatedResourceAccountIndex reply =
        wallet.getDelegatedResourceAccountIndex(address);
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L76-81)
```java
        delegateResource(ownerAddress, receiverAddress, true,
            delegateBalance, lock, lockPeriod);

        ownerCapsule.addDelegatedFrozenV2BalanceForBandwidth(delegateBalance);
        ownerCapsule.addFrozenBalanceForBandwidthV2(-delegateBalance);
        break;
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L277-280)
```java
  @Override
  public long calcFee() {
    return 0;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L313-316)
```java
    //modify DelegatedResourceAccountIndexStore
    delegatedResourceAccountIndexStore.delegateV2(ownerAddress, receiverAddress,
        dynamicPropertiesStore.getLatestBlockHeaderTimestamp());

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
