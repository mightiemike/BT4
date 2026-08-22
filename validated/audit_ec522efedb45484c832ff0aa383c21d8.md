### Title
Unbounded full-table scan and O(n log n) sort on every call to `getpaginatedassetissuelist`, regardless of requested page size - (`chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java`)

### Summary
`AssetIssueStore.getAssetIssuesPaginated(offset, limit)` always calls `getAllAssetIssues()`, which performs a full unbounded iteration of the on-disk `asset-issue` column family via `TronStoreWithRevoking.iterator()`, and then sorts the *entire* result set before slicing out the requested page. Because the cost of every call is proportional to the total number of asset issues in the DB (`O(n log n)`) rather than to the requested `limit`, an unprivileged remote caller can repeatedly invoke the endpoint with cheap, valid parameters to force expensive full scans/sorts on every request, degrading node responsiveness.

### Finding Description
The relevant code: [1](#0-0) 

`getAllAssetIssues()` streams the full `iterator()` of the underlying store with no bound. `getAssetIssuesPaginated(offset, limit)` then:
1. Rejects only negative `offset`/`limit`.
2. Checks `assetIssueList.size() <= offset` — a cheap `O(1)`/`O(n)` check that still requires the full list to already be materialized from disk.
3. Sorts the *entire* `assetIssueList` (`O(n log n)`) even when `limit` is small (e.g. `1`).
4. Only after sorting does it clamp `limit` to `ASSET_ISSUE_COUNT_LIMIT_MAX` and take a `subList`.

This method is reachable from unauthenticated, unprivileged callers via:
- HTTP: `GetPaginatedAssetIssueListServlet.doGet/doPost` → `Wallet.getAssetIssueList(offset, limit)` → `AssetIssueStore.getAssetIssuesPaginated`. [2](#0-1) 
- The same code path is duplicated for the Solidity and PBFT read-only interfaces (`GetPaginatedAssetIssueListOnSolidityServlet`, `GetPaginatedAssetIssueListOnPBFTServlet`) and for the gRPC `GetPaginatedAssetIssueList` RPC in `RpcApiService`.
- `Wallet.getAssetIssueList(long offset, long limit)` performs no additional bounding before delegating to the store. [3](#0-2) 

No actuator, signature, permission, energy/bandwidth, or fee check applies here since this is a read-only query endpoint, not a broadcast transaction — the caller pays nothing and needs no account or key. The only mitigating control present is `RateLimiterServlet`, which enforces a per-endpoint QPS/global-preemptible rate limit and per-IP QPS limiting depending on configured adapter. [4](#0-3) 
This limiter throttles *request count*, not CPU cost per request — it does not scale with `n` (table size) or account for the fact that each admitted request costs `O(n log n)` regardless of the tiny `limit` requested. With a sufficiently large asset-issue table and multiple source IPs (or an adapter configuration keyed by global QPS rather than strict single-token serialization), an attacker can still sustain a stream of admitted requests that each trigger a full scan-and-sort, and this cost is entirely independent of the `offset`/`limit` values chosen, so no cheap "small page" request is actually cheap.

### Impact Explanation
This is a CPU-exhaustion Denial-of-Service against the RPC-API layer (`GetPaginatedAssetIssueList` HTTP/gRPC endpoints and their Solidity/PBFT mirrors). Each request forces a full read of the entire `asset-issue` store plus a comparator-based sort of every entry, so cost scales with total assets on-chain, not with the requested page. Sustained concurrent requests can consume CPU across the node's worker threads, slowing or stalling other RPC-API consumers on the same node. This falls under "DoS via RPC-API" in the stated impact classes.

### Likelihood Explanation
- No privilege, signature, fee, or account required — this is a plain HTTP/gRPC read call.
- Preconditions: a node with a large asset-issue table (which grows organically as more assets are issued on mainnet over time, so this is not attacker-controlled but is a real precondition already true on production TRON networks).
- The existing mitigation (`RateLimiterServlet`/`GlobalRateLimiter`) limits request rate but does not scale the admitted rate down as `n` (table size) grows, nor does it account for the fact that "small limit" requests are just as expensive as "large limit" ones; it is default configuration and does not close this gap.
- Attack is trivially repeatable and requires no state change, so it can be sustained indefinitely at whatever rate the rate limiter admits.

### Recommendation
Avoid materializing and fully sorting the entire asset-issue table on every paginated request:
- Maintain a persistent, incrementally-updated sorted index/order (e.g., keyed by name+order) so pagination can be served via bounded range reads instead of `getAllAssetIssues()` + full sort.
- Alternatively, cache the sorted list and invalidate/refresh only when asset issues are added/updated, rather than recomputing on every read call.
- Bound `getAllAssetIssues()`-derived work so its cost is proportional to `offset + limit` (e.g., partial sort / `nth_element`-style selection) rather than the full table size.

### Proof of Concept
JUnit-style benchmark demonstrating the O(n log n)-per-call cost independent of `limit`:
```java
// chainbase/src/test/java/org/tron/core/store/AssetIssueStorePaginationBenchmarkTest.java
@Test
public void paginatedCallCostScalesWithTableSizeNotLimit() {
  // Populate assetIssueStore with N asset issues (e.g. N = 200_000)
  for (int i = 0; i < N; i++) {
    assetIssueStore.put(keyFor(i), buildAssetIssueCapsule("asset" + i));
  }

  long start = System.nanoTime();
  List<AssetIssueCapsule> page = assetIssueStore.getAssetIssuesPaginated(N - 2, 1); // tiny limit
  long elapsed = System.nanoTime() - start;

  // elapsed time is dominated by getAllAssetIssues() (full iterator scan)
  // + list.sort() over all N entries, not by the requested limit=1.
  assertTrue(page.size() <= 1);
  // Repeat with limit = N to show cost is roughly the same as limit = 1,
  // proving cost is independent of requested page size.
}
```
Request-level reproduction: fire concurrent `GET /walletsolidity/getpaginatedassetissuelist?offset=<varying>&limit=1` requests from multiple source IPs against a node with a large asset-issue table, and observe sustained high CPU utilization on the node's HTTP worker threads proportional to table size rather than to the requested `limit=1`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java (L34-63)
```java
  public List<AssetIssueCapsule> getAllAssetIssues() {
    return Streams.stream(iterator())
        .map(Entry::getValue)
        .collect(Collectors.toList());
  }

  private List<AssetIssueCapsule> getAssetIssuesPaginated(List<AssetIssueCapsule> assetIssueList,
      long offset, long limit) {
    if (limit < 0 || offset < 0) {
      return null;
    }

    if (assetIssueList.size() <= offset) {
      return null;
    }
    assetIssueList.sort((o1, o2) -> {
      if (o1.getName() != o2.getName()) {
        return o1.getName().toStringUtf8().compareTo(o2.getName().toStringUtf8());
      }
      return Long.compare(o1.getOrder(), o2.getOrder());
    });
    limit = limit > ASSET_ISSUE_COUNT_LIMIT_MAX ? ASSET_ISSUE_COUNT_LIMIT_MAX : limit;
    long end = offset + limit;
    end = end > assetIssueList.size() ? assetIssueList.size() : end;
    return assetIssueList.subList((int) offset, (int) end);
  }

  public List<AssetIssueCapsule> getAssetIssuesPaginated(long offset, long limit) {
    return getAssetIssuesPaginated(getAllAssetIssues(), offset, limit);
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java (L20-46)
```java
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      long offset = Long.parseLong(request.getParameter("offset"));
      long limit = Long.parseLong(request.getParameter("limit"));
      fillResponse(offset, limit, visible, response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      String input = params.getParams();
      boolean visible = params.isVisible();
      PaginatedMessage.Builder build = PaginatedMessage.newBuilder();
      JsonFormat.merge(input, build, visible);
      fillResponse(build.getOffset(), build.getLimit(), visible, response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  private void fillResponse(long offset, long limit, boolean visible, HttpServletResponse response)
      throws IOException {
    AssetIssueList reply = wallet.getAssetIssueList(offset, limit);
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1546-1566)
```java
  public AssetIssueList getAssetIssueList(long offset, long limit) {
    AssetIssueList.Builder builder = AssetIssueList.newBuilder();

    List<AssetIssueCapsule> assetIssueList =
        getAssetIssueStoreFinal(chainBaseManager.getDynamicPropertiesStore(),
            chainBaseManager.getAssetIssueStore(),
            chainBaseManager.getAssetIssueV2Store()).getAssetIssuesPaginated(offset, limit);

    if (CollectionUtils.isEmpty(assetIssueList)) {
      return null;
    }

    BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
    assetIssueList.forEach(
        issueCapsule -> {
          processor.updateUsage(issueCapsule);
          builder.addAssetIssue(issueCapsule.getInstance());
        }
    );
    return builder.build();
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
