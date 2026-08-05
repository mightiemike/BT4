### Title
Unbounded memory allocation in `getAssetIssuesPaginated` due to full-table materialization - (File: chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java)

### Summary
`AssetIssueStore.getAssetIssuesPaginated(offset, limit)` calls `getAllAssetIssues()`, which streams the entire underlying RocksDB column family into an in-memory `List<AssetIssueCapsule>` before sorting and slicing to the requested page. Since asset issuance (`AssetIssueContract`) is a normal, low fixed-cost public transaction with no cap on total number of distinct assets, an attacker can grow the on-chain asset count arbitrarily, causing every future call to `GET /wallet/getpaginatedassetissuelist` (and the gRPC equivalent) to allocate memory proportional to the total asset count rather than the requested page size.

### Finding Description
The HTTP endpoint `GetPaginatedAssetIssueListServlet.fillResponse` calls `wallet.getAssetIssueList(offset, limit)` [1](#0-0) , which in turn calls `AssetIssueStore.getAssetIssuesPaginated(offset, limit)` [2](#0-1) .

That method is implemented as:
```java
public List<AssetIssueCapsule> getAssetIssuesPaginated(long offset, long limit) {
    return getAssetIssuesPaginated(getAllAssetIssues(), offset, limit);
}
``` [3](#0-2) 

`getAllAssetIssues()` fully materializes the store contents:
```java
public List<AssetIssueCapsule> getAllAssetIssues() {
    return Streams.stream(iterator())
        .map(Entry::getValue)
        .collect(Collectors.toList());
}
``` [4](#0-3) 

The private overload then sorts the *entire* materialized list before slicing it down to `limit` (capped at `ASSET_ISSUE_COUNT_LIMIT_MAX`):
```java
assetIssueList.sort(...);
...
return assetIssueList.subList((int) offset, (int) end);
``` [5](#0-4) 

The rate limiter on the servlet (`RateLimiterServlet`) only throttles request frequency, it does not bound the cost of a single request, which is what scales with total chain state. The response size itself is capped by `ASSET_ISSUE_COUNT_LIMIT_MAX`, but the *allocation/sort cost incurred to produce that response* is not — it is O(N) in the total number of issued assets, not O(limit). Asset issuance itself only costs the fixed configurable `AssetIssueFee` (via `DynamicPropertiesStore.getAssetIssueFee()`, consumed in `AssetIssueActuator.execute`) [6](#0-5) , and there is no on-chain cap on the total number of distinct assets that can be issued — each issuance costs the issuer a fixed fee regardless of the amplification imposed on all future readers of `getpaginatedassetissuelist`.

### Impact Explanation
Each call to the pagination endpoint allocates a full `ArrayList` of `AssetIssueCapsule` objects for every asset ever issued on chain, then performs an O(N log N) sort over that list, before slicing out the small requested page. As total asset count N grows (driven purely by unprivileged, cheap `AssetIssueContract` transactions), the CPU and heap cost of every subsequent pagination call grows linearly/super-linearly with N instead of with `limit`. On a public full/solidity node exposing this HTTP/gRPC API, repeated pagination requests against a bloated `AssetIssueStore`/`AssetIssueV2Store` can produce elevated heap churn and GC pause time, degrading responsiveness for all API consumers on that node. This is a resource-exhaustion / DoS-adjacent concern rather than a consensus, funds, or state-integrity issue.

### Likelihood Explanation
Feasible: any account can submit `AssetIssueContract` transactions for the fixed `AssetIssueFee`, and there is no store-level cap preventing accumulation of a very large number of distinct assets over time (multiple accounts/addresses can each issue since same-owner reissue restrictions don't cap total distinct issuers). The read path (`getpaginatedassetissuelist`) is a standard public HTTP/gRPC API with only a request-rate limiter, not a per-request cost/complexity guard, so the attack requires no special privileges — repeated low-cost issuance over time plus normal public API polling by any client (or the attacker) triggers the amplified cost.

### Recommendation
Avoid materializing and sorting the full asset table on every paginated read:
- Maintain a persistently sorted index (e.g., a secondary key ordered by name/order) so pagination can seek directly to the offset via the underlying iterator instead of loading and sorting all records in memory, or
- Cache the sorted list and invalidate/rebuild it only when the store changes (e.g., on new asset issuance), or
- At minimum, bound `getAllAssetIssues()` usage in the pagination path by iterating with an early-exit once `offset + limit` sorted candidates are found, or track a maintained max asset count with sanity limits so operators can detect/mitigate abnormal growth.

### Proof of Concept
Java integration test plan (JMH-style or plain heap-measurement test) in `framework/src/test`:
```java
// AssetIssuePaginationMemoryTest.java
// 1. Set up an in-memory/temp ChainBaseManager with AssetIssueStore + AssetIssueV2Store.
// 2. For N in {1_000, 10_000, 100_000}:
//    a. Bulk-insert N AssetIssueCapsule entries directly via assetIssueStore.put(...)
//       to simulate N prior AssetIssueContract transactions.
//    b. Force a GC and record Runtime.getRuntime().totalMemory()-freeMemory() as baselineHeap.
//    c. Call assetIssueStore.getAssetIssuesPaginated(0, 10) (fixed small limit).
//    d. Record heap delta and elapsed time immediately after the call.
// 3. Assert that heap delta and elapsed time grow ~linearly (or worse) with N,
//    even though the requested `limit` (10) and returned list size (10) are constant.
//    Expected (vulnerable) result: heapDelta(N=100_000) >> heapDelta(N=1_000)
//    despite identical response size — demonstrating allocation scales with total
//    chain state (N) rather than with the response/page size (limit).
```
This demonstrates that `getAssetIssuesPaginated`/`getAllAssetIssues` [7](#0-6)  violates the invariant that public read-path memory allocation should scale with response size, not total on-chain state size.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java (L44-46)
```java
  private void fillResponse(long offset, long limit, boolean visible, HttpServletResponse response)
      throws IOException {
    AssetIssueList reply = wallet.getAssetIssueList(offset, limit);
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1546-1552)
```java
  public AssetIssueList getAssetIssueList(long offset, long limit) {
    AssetIssueList.Builder builder = AssetIssueList.newBuilder();

    List<AssetIssueCapsule> assetIssueList =
        getAssetIssueStoreFinal(chainBaseManager.getDynamicPropertiesStore(),
            chainBaseManager.getAssetIssueStore(),
            chainBaseManager.getAssetIssueV2Store()).getAssetIssuesPaginated(offset, limit);
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

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L56-76)
```java
  public boolean execute(Object result) throws ContractExeException {
    TransactionResultCapsule ret = (TransactionResultCapsule) result;
    if (Objects.isNull(ret)) {
      throw new RuntimeException(ActuatorConstant.TX_RESULT_NULL);
    }

    long fee = calcFee();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    AssetIssueStore assetIssueStore = chainBaseManager.getAssetIssueStore();
    AssetIssueV2Store assetIssueV2Store = chainBaseManager.getAssetIssueV2Store();
    AccountStore accountStore = chainBaseManager.getAccountStore();
    try {
      AssetIssueContract assetIssueContract = any.unpack(AssetIssueContract.class);
      byte[] ownerAddress = assetIssueContract.getOwnerAddress().toByteArray();
      AssetIssueCapsule assetIssueCapsule = new AssetIssueCapsule(assetIssueContract);
      AssetIssueCapsule assetIssueCapsuleV2 = new AssetIssueCapsule(assetIssueContract);
      long tokenIdNum = dynamicStore.getTokenIdNum();
      tokenIdNum++;
      assetIssueCapsule.setId(Long.toString(tokenIdNum));
      assetIssueCapsuleV2.setId(Long.toString(tokenIdNum));
      dynamicStore.saveTokenIdNum(tokenIdNum);
```
