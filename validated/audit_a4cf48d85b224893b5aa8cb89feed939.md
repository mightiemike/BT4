### Title
Public `/wallet/getassetissuelistbyname` endpoint triggers unbounded full-table scan of all asset issues - ([File: framework/src/main/java/org/tron/core/services/http/GetAssetIssueListByNameServlet.java])

### Finding Description
`GetAssetIssueListByNameServlet.fillResponse()` takes an attacker-controlled `value` (asset name) parameter directly from an unauthenticated HTTP request and passes it to `wallet.getAssetIssueListByName(ByteString.copyFrom(...))` with no length/format restriction beyond hex decoding. [1](#0-0) 

`Wallet.getAssetIssueListByName()` (and the sibling `getAssetIssueList()` used by `GetAssetIssueListServlet`) rely on `AssetIssueStore.getAllAssetIssues()`, which fetches **every** asset-issue record from the store via a full iterator, materializes it into a `List<AssetIssueCapsule>`, and only then filters/searches by name in memory: [2](#0-1) 

There is no index or bounded lookup keyed by asset name — the store is keyed by asset ID/token ID, so any name-based query (including a name that doesn't exist, or a common/short prefix) requires scanning the entire dataset. The only protection at the HTTP layer is `RateLimiterServlet`, which throttles request *rate*, not the *cost* of each individual accepted request. [3](#0-2) 

### Impact Explanation
Each accepted request (even within the rate limit) forces a full iteration and deserialization of the entire `asset-issue` column family plus a linear name comparison over the resulting list. As the number of issued TRC10 assets grows, the cost per request grows linearly, so an unprivileged caller sending repeated requests (at the rate limiter's allowed cadence) can sustain elevated CPU and memory (temporary List allocation of all capsules) load on a full node, degrading service for other API consumers. This is a public-cost-control violation: cheap-looking name lookups are actually O(N) full scans with no pagination/index bound.

### Likelihood Explanation
The endpoint is a completely public, unauthenticated HTTP API (`/wallet/getassetissuelistbyname`) reachable by anyone with network access to a full/solidity node's HTTP API. No special privileges, on-chain transactions, or fees are required — a bare GET/POST with an empty or non-matching `value` parameter reliably triggers the full scan every time. The rate limiter only bounds request frequency, not the amount of work per request, so the attack is trivially repeatable subject to that rate limit.

### Recommendation
- Add pagination/limits to `getAssetIssueListByName` (similar to `getAssetIssuesPaginated`) so lookups cannot force scanning/returning unbounded result sets.
- Consider maintaining a secondary index (e.g., name -> asset id mapping) to avoid full-table iteration for name lookups.
- Cap or reject queries with very short/common prefixes, and enforce a maximum record scan/response size at the API layer independent of the generic rate limiter.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/services/http/GetAssetIssueListByNameServletTest.java (extend existing test)
@Test
public void testUnboundedScanOnNonExistentName() throws Exception {
  // Arrange: populate AssetIssueStore with N (e.g. 100_000) synthetic AssetIssueCapsule entries
  // via chainbaseManager.getAssetIssueStore().put(...), each with distinct random names.

  AssetIssueStore spyStore = Mockito.spy(chainbaseManager.getAssetIssueStore());
  // inject spyStore into wallet under test via reflection

  long start = System.nanoTime();
  wallet.getAssetIssueListByName(ByteString.copyFromUtf8("nonexistent_name_prefix"));
  long elapsed = System.nanoTime() - start;

  // Assert: getAllAssetIssues() was invoked (full iteration), confirming O(N) scan
  Mockito.verify(spyStore, Mockito.atLeastOnce()).getAllAssetIssues();
  // Assert: elapsed time scales with N (repeat with N and 10N, expect ~10x time),
  // demonstrating no pagination/index bound exists for name lookups.
}
```
Expected result: `getAllAssetIssues()` is called on every invocation regardless of whether the name matches anything, and latency scales linearly with total asset-issue count, confirming the unbounded scan.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetAssetIssueListByNameServlet.java (L19-19)
```java
public class GetAssetIssueListByNameServlet extends RateLimiterServlet {
```

**File:** framework/src/main/java/org/tron/core/services/http/GetAssetIssueListByNameServlet.java (L45-56)
```java
  private void fillResponse(boolean visible, String value, HttpServletResponse response)
      throws IOException {
    if (visible) {
      value = Util.getHexString(value);
    }
    AssetIssueList reply = wallet.getAssetIssueListByName(ByteString.copyFrom(
        ByteArray.fromHexString(value)));
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java (L31-38)
```java
  /**
   * get all asset issues.
   */
  public List<AssetIssueCapsule> getAllAssetIssues() {
    return Streams.stream(iterator())
        .map(Entry::getValue)
        .collect(Collectors.toList());
  }
```
