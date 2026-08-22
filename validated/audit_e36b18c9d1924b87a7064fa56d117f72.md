### Title
Unbounded full-table scan + sort in `AssetIssueStore.getAssetIssuesPaginated` allows CPU-exhaustion DoS via `GET /wallet/getpaginatedassetissuelist` - ([File: chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java])

### Summary
`GetPaginatedAssetIssueListServlet.fillResponse` forwards attacker-controlled `offset`/`limit` straight to `Wallet.getAssetIssueList(long,long)`, which calls `AssetIssueStore.getAssetIssuesPaginated(long,long)`. That method unconditionally materializes **every** asset issue on chain via `getAllAssetIssues()` (a full `TronStoreWithRevoking` iterator traversal across all revoking layers) and then runs a full `List.sort` over the entire result set before slicing out the tiny requested page. Since the per-request cost is O(N log N) regardless of `limit`, and the only protection is a flat QPS rate limiter, an attacker can drive sustained CPU exhaustion cheaply.

### Finding Description
- HTTP entrypoint: `GetPaginatedAssetIssueListServlet.doGet`/`doPost` parses `offset`/`limit` from the request with no bounds validation and calls `fillResponse`, which invokes `wallet.getAssetIssueList(offset, limit)`. [1](#0-0) 
- That flows into `AssetIssueStore.getAssetIssuesPaginated(long offset, long limit)`, which delegates to the private overload with `getAllAssetIssues()` as the source list: [2](#0-1) 
- `getAllAssetIssues()` streams the entire underlying store via `iterator()` (a `TronStoreWithRevoking` traversal spanning all revoking layers) and collects it into a `List`, regardless of the requested `limit`: [3](#0-2) 
- The private `getAssetIssuesPaginated(List, long, long)` then performs a full `List.sort` over the whole list (comparator based on asset name/order) before slicing the small page (`limit` is only capped for the *output* slice, not for the upstream scan/sort cost): [4](#0-3) 
- The only gate in front of this endpoint is `RateLimiterServlet`, whose default adapter (`DefaultBaseQqsAdapter`) is a pure QPS limiter that meters requests, not computational cost: [5](#0-4) 

None of the existing checks (signature validation, permission weights, energy/bandwidth accounting, fork gates) apply here because this is a pure read-only HTTP GET/POST query endpoint, not a transaction — it bypasses all fee/energy metering entirely. The QPS limiter throttles request *count*, not request *cost*, so each allowed request still costs the server O(N log N) regardless of the `limit=1` in the query string, violating faithful metering: an attacker pays for one cheap request but the server performs work proportional to the entire asset-issue table size.

### Impact Explanation
This is a DoS via RPC-API. Since asset issuance is permissionless and cheap on TRON (any funded account can create an `AssetIssueContract`), the total number of `AssetIssueCapsule` records on a live/long-running chain can be substantial, making each call to this endpoint expensive. Because the endpoint offers pagination only nominally (`limit` doesn't reduce the underlying scan/sort cost), an attacker can send many small-`limit` requests near the QPS ceiling and force the full node to repeatedly execute full-store iteration plus `O(N log N)` sorting, causing sustained CPU exhaustion on the FullNode/SolidityNode HTTP API layer (the same store/servlet logic is reused by `GetPaginatedAssetIssueListOnSolidityServlet` and `GetPaginatedAssetIssueListOnPBFTServlet`), degrading availability for legitimate RPC users.

### Likelihood Explanation
No privileged role is required — any anonymous HTTP client can call `GET /wallet/getpaginatedassetissuelist?offset=0&limit=1` under default node configuration (no auth, default `RateLimiterServlet`/`DefaultBaseQqsAdapter` settings). The attack is trivially repeatable and requires no on-chain fee payment, only that the node has a large-enough asset-issue table (which grows organically as it's a public feature); an attacker could also inflate the table themselves over time by issuing many low-cost assets, though a large existing table alone suffices. This significantly lowers the bar compared to typical resource-exhaustion attacks that require paying transaction fees.

### Recommendation
- Cache a sorted view of asset issues (e.g., an in-memory sorted index maintained incrementally on issuance/update) instead of re-fetching and re-sorting the entire table on every request.
- Alternatively, use a database-level range query with server-side ordering/pagination (if the underlying store supports sorted iteration/prefix scans) so the cost scales with `offset + limit`, not with total table size.
- Enforce a strict, low ceiling on `offset` and validate it against a maintained count, and consider a dedicated, stricter rate limit for this expensive endpoint independent of the generic QPS limiter.

### Proof of Concept
```java
// JUnit-style illustration (framework/src/test/java/org/tron/core/services/http/... )
@Test
public void testPaginatedAssetIssueScanCostIndependentOfLimit() {
  // Seed AssetIssueStore with e.g. 50_000 AssetIssueCapsule entries
  for (int i = 0; i < 50_000; i++) {
    assetIssueStore.put(key(i), buildAssetIssueCapsule(i));
  }

  long start = System.nanoTime();
  List<AssetIssueCapsule> page = assetIssueStore.getAssetIssuesPaginated(0, 1);
  long elapsed = System.nanoTime() - start;

  // Assert: elapsed time/CPU scales with store size (O(N log N)),
  // not with requested limit=1, demonstrating disproportionate server cost
  // per allowed request under RateLimiterServlet's default QPS ceiling.
  assertEquals(1, page.size());
  // elapsed grows near-linearithmically as N increases in repeated runs
}
```
Request-level reproduction: repeatedly send `GET /wallet/getpaginatedassetissuelist?offset=0&limit=1` at the default QPS ceiling against a node whose `AssetIssueStore` contains tens of thousands of entries, and observe per-request CPU time tracking the full table size rather than the requested `limit`.

### Citations

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

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/adapter/DefaultBaseQqsAdapter.java (L1-23)
```java
package org.tron.core.services.ratelimiter.adapter;

import org.tron.core.services.ratelimiter.RuntimeData;
import org.tron.core.services.ratelimiter.strategy.QpsStrategy;

public class DefaultBaseQqsAdapter implements IRateLimiter {

  private QpsStrategy strategy;

  public DefaultBaseQqsAdapter(String paramString) {
    this.strategy = new QpsStrategy(paramString);
  }

  @Override
  public boolean tryAcquire(RuntimeData data) {
    return strategy.tryAcquire();
  }

  @Override
  public boolean acquire(RuntimeData data) {
    return strategy.acquire();
  }
}
```
