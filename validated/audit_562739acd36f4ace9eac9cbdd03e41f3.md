### Title
Unauthenticated CPU DoS via forced full asset-store sort on every paginated asset issue request - (File: chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java)

### Summary
The public HTTP endpoint `/wallet/getpaginatedassetissuelist` (`GetPaginatedAssetIssueListServlet`) forwards attacker-controlled `offset`/`limit` values directly to `Wallet.getAssetIssueList`, which calls `AssetIssueStore.getAssetIssuesPaginated`. That method always loads the entire asset-issue store into memory and sorts it before applying any limit clamp, so the limit parameter cannot reduce the actual computational cost of a request.

### Finding Description
`GetPaginatedAssetIssueListServlet.doGet`/`doPost` parse `offset` and `limit` as raw `long` values from the request with no bounds checking beyond `Long.parseLong` and pass them straight to `wallet.getAssetIssueList(offset, limit)`: [1](#0-0) 

That call reaches `AssetIssueStore.getAssetIssuesPaginated(long offset, long limit)`, which always calls `getAllAssetIssues()` (iterating and materializing the full DB store into a `List`), unconditionally runs a full `List.sort()` over that entire list, and only afterward clamps `limit` to `ASSET_ISSUE_COUNT_LIMIT_MAX`: [2](#0-1) 

Because the clamp on line 55 happens strictly after the `sort()` call on line 49, requesting `limit=1` or `limit=Long.MAX_VALUE` incurs identical O(n log n) cost proportional to the total number of asset issues stored on-chain, not the requested page size. The only mitigating control is `RateLimiterServlet`, which throttles request rate but does not account for or cap the per-request computational cost: [3](#0-2) 
There is no per-request work budget, no caching of the sorted list, and no authentication/fee requirement on this HTTP endpoint — it is reachable by any anonymous client.

### Impact Explanation
Repeated calls to `/wallet/getpaginatedassetissuelist` force the full node to repeatedly materialize and sort the complete asset-issue store from disk/memory on every single request regardless of the tiny `limit` requested, wasting CPU proportional to total assets issued network-wide. This matches the "DoS via RPC-API" bounty class: an unprivileged, unauthenticated remote attacker can degrade node responsiveness/availability by issuing a sustained (rate-limiter-bounded) stream of cheap requests, each of which is disproportionately expensive server-side.

### Likelihood Explanation
No privileged role, signed transaction, or fee is required — this is a plain HTTP GET/POST to a full node's open API. The only friction is `RateLimiterServlet`'s request-rate throttling, which limits requests/second but not cost/request, so an attacker can still sustain continuous expensive sort operations within the allowed rate, and can also fan the attack out across multiple public full nodes. As the asset-issue store grows over time (irreversibly, since TRC-10 assets are never deleted), the fixed cost of each request grows, making this increasingly effective without any change in attacker effort.

### Recommendation
Reorder the logic in `AssetIssueStore.getAssetIssuesPaginated` so `limit`/`offset` are validated and clamped to sane bounds (e.g., against `ASSET_ISSUE_COUNT_LIMIT_MAX` and the current store size) before doing any sort, and avoid re-sorting/re-materializing the entire store on every call — e.g., maintain a persistently sorted index or cache the sorted list keyed by a change counter, and use a partial/selection-based extraction (e.g., a bounded heap or `Collections.nthElement`-style approach) instead of a full `List.sort()` when only a small page is requested. Additionally, consider adding server-side hard caps on `limit` (independent of client input) enforced prior to any O(n log n) work.

### Proof of Concept
```java
// JUnit-style benchmark demonstrating cost is independent of requested limit
@Test
public void testPaginatedSortCostIndependentOfLimit() {
  AssetIssueStore store = ...; // populated with N large asset issues

  long start1 = System.nanoTime();
  store.getAssetIssuesPaginated(0, 1); // tiny limit
  long cost1 = System.nanoTime() - start1;

  long start2 = System.nanoTime();
  store.getAssetIssuesPaginated(0, Long.MAX_VALUE - 1); // huge limit
  long cost2 = System.nanoTime() - start2;

  // Both calls do a full getAllAssetIssues() + sort() over all N entries;
  // cost1 ~= cost2, both scale with N (O(n log n)), not with requested limit.
  assertTrue(Math.abs(cost1 - cost2) < cost1 * 0.5);
}
```
Raw HTTP reproduction: repeatedly issue
```
GET /wallet/getpaginatedassetissuelist?offset=0&limit=1
```
against a full node with a large TRC-10 asset population and observe CPU time dominated by the internal sort in `AssetIssueStore.getAssetIssuesPaginated`, scaling with total store size rather than the requested `limit=1`.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java (L15-15)
```java
public class GetPaginatedAssetIssueListServlet extends RateLimiterServlet {
```

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java (L20-29)
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
```

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
