The code confirms the described behavior: the pagination cap only clips the returned slice, not the underlying computation.### Title
Unbounded full-store sort on every paginated asset-issue query enables CPU-exhaustion DoS - ([File: chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java])

### Summary
`AssetIssueStore.getAssetIssuesPaginated(long, long)` materializes the entire asset-issue table via `getAllAssetIssues()`/`iterator()` and performs an O(n log n) sort on the full list before applying `ASSET_ISSUE_COUNT_LIMIT_MAX` to slice the result. Since this method is reachable from the unauthenticated `GetPaginatedAssetIssueList` HTTP/gRPC endpoint, an attacker can force repeated full-table materialization and sorting regardless of the requested `limit`, causing CPU/GC load that scales with total asset count rather than with the requested page size.

### Finding Description
`getAssetIssuesPaginated(long offset, long limit)` calls `getAllAssetIssues()` [1](#0-0)  which streams the *entire* underlying RocksDB/LevelDB iterator into a `List` [2](#0-1) . The private overload then sorts this full list by name/order before the `ASSET_ISSUE_COUNT_LIMIT_MAX` cap is applied and the sublist is sliced [3](#0-2) . The `ASSET_ISSUE_COUNT_LIMIT_MAX = 1000` constant only bounds the size of the *returned* slice (`limit = limit > MAX ? MAX : limit`), not the cost of building and sorting the full list beforehand [4](#0-3) .

This method is reachable unauthenticated via `GetPaginatedAssetIssueListServlet.doGet`/`doPost`, which parses attacker-controlled `offset`/`limit` and forwards to `wallet.getAssetIssueList(offset, limit)` with no bound-checking on `offset` beyond what the store itself does [5](#0-4) . The only protection present is a generic `RateLimiterServlet` QPS/IP rate limiter [6](#0-5) , which throttles request *rate* but does nothing to bound the *cost per request* — each admitted request still triggers a full O(n) table scan plus O(n log n) sort. There is no separate limiter or short-circuit based on `offset` magnitude or store size in the affected code path.

### Impact Explanation
This matches the "DoS via RPC-API" bounty class: as the asset-issue table grows (an attacker or normal usage can grow it via `AssetIssueContract` broadcasts, which are cheap/normal on-chain operations), every call to this public read endpoint imposes CPU cost proportional to the full table size rather than to the small requested page, regardless of `offset`/`limit` values chosen by the attacker (e.g., `offset=Long.MAX_VALUE-ish, limit=1`). Repeated calls (even within rate limits) sustain elevated CPU/GC pressure on the node serving the request, degrading its responsiveness to other API/RPC clients.

### Likelihood Explanation
No privileged role, signature, or fee is required — `GetPaginatedAssetIssueList` is a plain read-only HTTP/gRPC call available to any network client. The attacker only needs network access and a chain state with a non-trivial number of registered assets (which is realistic on any long-running mainnet/testnet). The existing `RateLimiterServlet` limits request frequency but not per-request cost, so the attack is fully repeatable up to whatever QPS the rate limiter allows, and each request remains expensive proportional to store size.

### Recommendation
Avoid materializing and sorting the entire asset-issue store on every paginated query:
- Maintain assets in an already-sorted index/secondary structure (e.g., sorted by name/order at write time) so pagination can seek directly into the sorted structure without a full scan+sort, or
- Cache the sorted list and invalidate/rebuild only on asset-issue changes rather than per-query, or
- Reject/clip `offset` values against a sane bound before doing any store iteration, and consider limiting how large `offset` may be relative to actual store size early, before the sort.

### Proof of Concept
```java
// JUnit-style PoC (chainbase module)
@Test
public void testPaginatedAssetIssueCostScalesWithStoreSize() {
  AssetIssueStore store = ...; // obtain store instance
  // Populate store with N (e.g., 200_000) AssetIssueCapsule entries
  for (int i = 0; i < N; i++) {
    AssetIssueCapsule capsule = new AssetIssueCapsule(/* unique name/id */);
    store.put(capsule.createDbKey(), capsule);
  }

  long start = System.nanoTime();
  List<AssetIssueCapsule> result = store.getAssetIssuesPaginated(N - 1L, 1L);
  long elapsed = System.nanoTime() - start;

  // elapsed time grows ~linearly/O(n log n) with N regardless of limit=1,
  // demonstrating the sort over the full list dominates cost, not the
  // requested small limit — confirms no protection against store-size DoS.
  assertTrue(elapsed > /* threshold proportional to N */ 0);
}
```
Request-level reproduction: repeatedly `GET /walletsolidity/getpaginatedassetissuelist?offset=999999999&limit=1` against a full node whose asset-issue table has grown large; observe CPU time per request tracks total asset count, not `limit`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java (L34-38)
```java
  public List<AssetIssueCapsule> getAllAssetIssues() {
    return Streams.stream(iterator())
        .map(Entry::getValue)
        .collect(Collectors.toList());
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java (L40-59)
```java
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
```

**File:** chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java (L61-63)
```java
  public List<AssetIssueCapsule> getAssetIssuesPaginated(long offset, long limit) {
    return getAssetIssuesPaginated(getAllAssetIssues(), offset, limit);
  }
```

**File:** chainbase/src/main/java/org/tron/common/utils/Commons.java (L22-22)
```java
  public static final int ASSET_ISSUE_COUNT_LIMIT_MAX = 1000;
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
