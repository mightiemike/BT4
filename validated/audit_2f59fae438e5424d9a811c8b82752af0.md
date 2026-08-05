### Title
Unbounded, unpriced iteration cost in `GetDelegatedResourceAccountIndexV2Servlet.doGet` scaling linearly with attacker-created delegation count - (`framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java`)

### Summary
`GetDelegatedResourceAccountIndexV2Servlet.doGet` calls `Wallet.getDelegatedResourceAccountIndexV2`, which resolves to `DelegatedResourceAccountIndexStore.getV2Index` → `getWithPrefix`, which performs a full DB prefix scan and in-memory sort proportional to the number of delegation records under an address [1](#0-0) [2](#0-1) . The only protection at this endpoint is a fixed QPS/global rate limiter that gates request *count*, not per-request work, so an attacker who cheaply creates N delegate relationships to/from one address can make each subsequent GET to this public endpoint cost O(N) DB reads, deserializations, and sorts, with no compensating fee since `DelegateResourceContract`'s `calcFee()` returns 0 [3](#0-2) .

### Finding Description
The GET path is: `GetDelegatedResourceAccountIndexV2Servlet.doGet` → `fillResponse` → `Wallet.getDelegatedResourceAccountIndexV2(address)` → `DelegatedResourceAccountIndexStore.getV2Index(address)` → `getWithPrefix(V2_FROM_PREFIX, V2_TO_PREFIX, address)`.

`getWithPrefix` executes two `prefixQuery` calls over the store, each returning **all** key/value pairs whose key starts with `prefix + address` [4](#0-3) . Each result entry is deserialized into a `DelegatedResourceAccountIndexCapsule`, collected into an `ArrayList`, and then sorted by timestamp with `Comparator.comparing` (an O(N log N) sort), for both the "to" list and the "from" list.

The number of entries N is fully attacker-controlled: any account can call `DelegateResourceContract` repeatedly (delegating to N distinct receiver addresses, or having N distinct senders delegate to it) at minimal cost — each delegate call only requires ≥1 TRX of already-frozen `FrozenV2` balance and writes a `V2_FROM_PREFIX`/`V2_TO_PREFIX` key pair via `DelegatedResourceAccountIndexStore.delegateV2` [5](#0-4) . `calcFee()` for the delegate actuator is `0` [3](#0-2) , and each delegated amount can be as small as 1 TRX (`TRX_PRECISION`) which is validated but not consumed/burned [6](#0-5) , so the on-chain cost to create N index entries is bounded only by ordinary bandwidth/energy transaction costs, not by the value delegated.

The only guard on the read path is `RateLimiterServlet.service`, which enforces per-endpoint and global QPS/permit limits before invoking `doGet` [7](#0-6) . This limiter is strictly request-count based (QPS/permits) and has no concept of the cost of an individual request's underlying work — it does not inspect N, throttle by response size, or reject overly large indexes. Consequently, once a request is admitted, its CPU/disk/state-iteration cost scales linearly (with an added log factor for sorting) in N, which is entirely attacker-chosen and unbounded by any fixed per-request budget.

### Impact Explanation
An attacker can grow a single address's delegation index to an arbitrarily large size (10, 1,000, 100,000+ entries) for a low, fixed on-chain cost per entry, then repeatedly hit the public `getdelegatedresourceaccountindexv2` HTTP/gRPC endpoint. Each admitted request forces the node to perform O(N) DB reads/deserializations and O(N log N) in-memory sorting, and returns an O(N)-sized JSON response. Because the fixed per-request QPS budget does not scale down as N grows, a small number of permitted requests per second against an inflated-index address can consume disproportionate CPU, memory, and I/O on full nodes, solidity nodes, and PBFT nodes (all of which expose this servlet) — a resource-exhaustion / DoS vector against a public read API.

### Likelihood Explanation
High feasibility: the attacker needs no privileges beyond an ordinary account with a small frozen `FrozenV2` balance. Creating N delegation index entries requires N `DelegateResourceContract` transactions, each costing only ordinary bandwidth/energy and at least 1 TRX of already-owned frozen balance to delegate (the balance is not spent, only redirected), so the attack is cheap and fully repeatable/scriptable. The endpoint is public and unauthenticated (rate-limited only by IP/QPS), so once the index is inflated, the attack can be repeated indefinitely.

### Recommendation
- Bound the response size / iteration cost of `DelegatedResourceAccountIndexStore.getWithPrefix` (and the V1 equivalent) with a hard cap on the number of entries returned, or add pagination parameters to `GetDelegatedResourceAccountIndexV2Servlet`/`Wallet.getDelegatedResourceAccountIndexV2`.
- Enforce a maximum number of delegation relationships per account in `DelegateResourceActuator.validate` (e.g., cap distinct receiver/owner counts), independent of the frozen-balance check.
- Consider a cost-aware rate limiter variant for this servlet that accounts for response size/iteration count rather than a flat QPS budget, or add a per-address cache with size limits/warnings when index sizes are anomalously large.

### Proof of Concept
```java
// Integration-style benchmark (JUnit) in framework/src/test/java/org/tron/core/services/http/
// 1. Setup: create OWNER account with frozen V2 bandwidth balance sufficient to
//    perform N delegations of 1 TRX each to N distinct RECEIVER accounts.
// 2. For each N in {0, 10, 1000, 100000}:
//    a. Execute N DelegateResourceContract transactions via DelegateResourceActuator
//       (as in DelegateResourceActuatorTest), each delegating 1 TRX to a fresh receiver.
//    b. Invoke GetDelegatedResourceAccountIndexV2Servlet.doGet with OWNER address,
//       timing wall-clock latency and capturing response payload size.
// 3. Assert:
//    - latency(N=100000) / latency(N=10) grows super-constant (e.g., >100x),
//      demonstrating no fixed per-request cost bound despite identical
//      RateLimiterServlet QPS budget applied to both requests.
//    - Confirm DelegateResourceActuator.calcFee() == 0 for every delegation transaction,
//      i.e., no compensating fee funds the later read cost.
// Expected: latency/CPU scale ~linearly (with log factor from sort) in N, proving
// the endpoint's cost is unbounded relative to the fixed QPS-based rate limiter.
```

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java (L60-63)
```java
  private void fillResponse(ByteString address, boolean visible, HttpServletResponse response)
      throws IOException {
    DelegatedResourceAccountIndex reply =
        wallet.getDelegatedResourceAccountIndexV2(address);
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

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L147-150)
```java
    long delegateBalance = delegateResourceContract.getBalance();
    if (delegateBalance < TRX_PRECISION) {
      throw new ContractValidateException("delegateBalance must be greater than or equal to 1 TRX");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L277-280)
```java
  @Override
  public long calcFee() {
    return 0;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L313-315)
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
