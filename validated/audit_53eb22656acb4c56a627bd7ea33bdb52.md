### Title
Unbounded full-store materialization in paginated asset issue list endpoint - ([File: framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java])

### Summary
The public `/wallet/getpaginatedassetissuelist` HTTP endpoint accepts an unprivileged `offset`/`limit` request and forwards it to `Wallet.getAssetIssueList(offset, limit)`, which calls `AssetIssueStore.getAssetIssuesPaginated(offset, limit)`. This method always calls `getAllAssetIssues()` to load **every** asset issue record from the store into memory and sort it, before the `limit` cap (`ASSET_ISSUE_COUNT_LIMIT_MAX`) is applied. Pagination bounds are enforced only after the expensive full-scan/sort work has already occurred.

### Finding Description
`GetPaginatedAssetIssueListServlet.fillResponse()` reads attacker-controlled `offset` and `limit` from the HTTP request and calls `wallet.getAssetIssueList(offset, limit)` [1](#0-0) . This flows into `AssetIssueStore.getAssetIssuesPaginated(long offset, long limit)`, which unconditionally calls `getAllAssetIssues()`:

```java
public List<AssetIssueCapsule> getAssetIssuesPaginated(long offset, long limit) {
  return getAssetIssuesPaginated(getAllAssetIssues(), offset, limit);
}
``` [2](#0-1) 

`getAllAssetIssues()` streams the entire underlying DB iterator into a `List`, i.e., an O(N) read of the full asset-issue store regardless of the requested `limit` [3](#0-2) . Only after this full materialization does the private overload sort the entire list and apply `limit = limit > ASSET_ISSUE_COUNT_LIMIT_MAX ? ASSET_ISSUE_COUNT_LIMIT_MAX : limit` before slicing the sublist [4](#0-3) . The `ASSET_ISSUE_COUNT_LIMIT_MAX` cap bounds only the size of the *response*, not the cost of computing it — the full-store read and full-list sort happen no matter how small `limit` is set (even `limit=1`).

The only protection at the HTTP layer is generic `RateLimiterServlet` request-rate throttling, not a cost-proportional guard tied to store size, so an attacker sending pagination requests (even with small limit) still forces a full store scan + sort per request.

### Impact Explanation
Each call to `/wallet/getpaginatedassetissuelist` (GET or POST) forces the node to read and sort all asset-issue records in the database, an O(N) memory/CPU/disk operation, while returning only a small, bounded page to the caller. An unprivileged, unauthenticated caller can repeatedly issue such requests to amplify their cost relative to a normal paginated request, degrading node responsiveness for legitimate API/RPC consumers on the same node. This is a public-API resource-exhaustion / underpriced-work issue rather than a fund-theft or consensus issue, and its severity scales with the number of asset issues (`N`) registered on the chain and request frequency (bounded by whatever rate limiting is configured for this servlet).

### Likelihood Explanation
The endpoint is reachable by any unauthenticated user with HTTP access to the node's API port; it requires no special permission, contract deployment, or governance role — this matches the "unprivileged attacker" criterion exactly. The only friction is the generic servlet rate limiter, which limits request rate but not per-request cost, so the exploit is fully repeatable at whatever rate the limiter allows. As `N` (number of registered asset issues on the network) grows over time, the per-request cost of this endpoint grows proportionally for every caller, including legitimate ones.

### Recommendation
Restructure `AssetIssueStore.getAssetIssuesPaginated` to avoid materializing/sorting the entire store when only a bounded window is requested — e.g., maintain an index sorted by name/order (or use the underlying RocksDB iterator's ordering) and iterate only up to `offset + min(limit, ASSET_ISSUE_COUNT_LIMIT_MAX)` entries, short-circuiting further store reads. Alternatively, cache the sorted asset-issue list and invalidate/refresh it only when the store changes, rather than rebuilding it from `getAllAssetIssues()` on every paginated request.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/store/AssetIssueStoreCostTest.java
@Test
public void testPaginationTriggersFullStoreLoad() {
  AssetIssueStore store = spy(context.getBean(AssetIssueStore.class));
  // populate store with N (e.g., 100_000) AssetIssueCapsule entries
  for (int i = 0; i < 100_000; i++) {
    store.put(makeKey(i), makeAssetIssueCapsule(i));
  }

  // Request a tiny page: offset=0, limit=1
  List<AssetIssueCapsule> page = store.getAssetIssuesPaginated(0, 1);

  // Assert getAllAssetIssues() (full scan) was invoked despite limit=1
  verify(store, times(1)).getAllAssetIssues();
  assertEquals(1, page.size());

  // Optionally profile: measure elapsed time/heap for offset=0, limit=1
  // vs offset=0, limit=ASSET_ISSUE_COUNT_LIMIT_MAX and show they are
  // statistically identical (both O(N)), proving limit does not bound cost.
}
```
Expected assertions: `getAllAssetIssues()` is called on every invocation of `getAssetIssuesPaginated`, and profiling shows request latency/memory scales with total store size `N`, not with the requested `limit`.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java (L44-52)
```java
  private void fillResponse(long offset, long limit, boolean visible, HttpServletResponse response)
      throws IOException {
    AssetIssueList reply = wallet.getAssetIssueList(offset, limit);
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
  }
```

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
