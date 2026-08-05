### Title
Offset-based pagination in `GetPaginatedAssetIssueListServlet` skips/duplicates assets when re-sorted list is mutated between calls - (File: framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java)

### Summary
`Wallet.getAssetIssueList(offset, limit)` delegates to `AssetIssueStore.getAssetIssuesPaginated`, which re-fetches and re-sorts the *entire* live asset-issue list by name on every single call before slicing `[offset, offset+limit)`. Because any unprivileged account can broadcast an `AssetIssueContract` between two paginated requests, and the new asset's name can sort earlier alphabetically than existing entries, the index-to-asset mapping shifts between calls, causing entries to be skipped or duplicated across a client's page-by-page enumeration.

### Finding Description
The HTTP entrypoint `GetPaginatedAssetIssueListServlet.doGet`/`doPost` accepts `offset`/`limit` from any caller with no privilege requirement, and calls `wallet.getAssetIssueList(offset, limit)`: [1](#0-0) 

That method invokes `AssetIssueStore.getAssetIssuesPaginated(offset, limit)`: [2](#0-1) 

The store implementation re-fetches all current asset issues fresh and sorts them by `name` (then by `order`) on *every* call, before slicing the requested window: [3](#0-2) 

There is no snapshot token, cursor, block-height pin, or stable ordering key (e.g. creation sequence/ID) used for pagination — only a name-based sort recomputed against the live, mutable data set. Since asset issuance (`AssetIssueContract`) is a normal transaction any account can broadcast, an attacker (or even an unrelated legitimate user) can insert a new asset with a name that sorts alphabetically before assets already returned/pending in the client's enumeration window. This shifts every subsequent index by one position, so:
- A page-N request made *after* the insertion will return an asset that was already delivered in page N-1 (duplicate), or
- An asset that would have appeared at the boundary is pushed past the end of the previous page and skipped entirely on the next request (if the client assumes contiguous coverage based on previous offsets).

No authentication, rate limiting beyond the generic `RateLimiterServlet`, or ordering/versioning check prevents this — the servlet has no dependency on caller identity and the store performs no consistency check between calls.

### Impact Explanation
Any service that paginates through this endpoint (exchanges, indexers, block explorers building asset registries) to enumerate the full asset list can silently miss or double count `AssetIssueCapsule` entries whenever an asset is issued concurrently with the enumeration. This corrupts downstream data such as listing completeness for compliance/accounting or asset-discovery pipelines, without generating any error or detectable inconsistency to the client — a purely public API path.

### Likelihood Explanation
Highly feasible: issuing an asset is a normal, low-cost, permissionless transaction requiring only that the account meets the balance/TRX-burn requirement for `AssetIssueContract`, which is well within reach of any funded account. Pagination windows during multi-page enumeration commonly span more than one block/transaction propagation delay, making the interleaving condition realistic for busy chains or when a malicious actor deliberately issues cheap/no-op-named assets to disrupt indexers. This is repeatable at will by the attacker at any time.

### Recommendation
Use a stable pagination cursor that is invariant to concurrent insertions, e.g., paginate by immutable asset ID/creation order rather than re-sorting by mutable `name` on each call, or snapshot the full sorted list (or its ordering keys) at a fixed reference point (such as a block height or a monotonically increasing sequence number assigned at issuance) and paginate against that snapshot. Alternatively, expose a continuation token derived from the last returned asset's identity instead of a raw numeric offset, so subsequent requests resume strictly after that asset regardless of concurrent inserts.

### Proof of Concept
```java
// In AssetIssueStore / Wallet test context
@Test
public void testPaginationSkipsOrDuplicatesOnConcurrentIssuance() {
  // 1. Issue assets named "b_asset", "c_asset" (alphabetically after "a_asset")
  createAssetIssue("b_asset");
  createAssetIssue("c_asset");

  // 2. Client fetches page 0 with limit=1 -> expects "b_asset"
  AssetIssueList page0 = wallet.getAssetIssueList(0, 1);
  String firstName = page0.getAssetIssue(0).getName().toStringUtf8();
  assertEquals("b_asset", firstName);

  // 3. Attacker (unprivileged) issues a new asset "a_asset" that sorts before "b_asset"
  createAssetIssue("a_asset");

  // 4. Client fetches page 1 with offset=1, limit=1, expecting to continue after "b_asset"
  AssetIssueList page1 = wallet.getAssetIssueList(1, 1);
  String secondName = page1.getAssetIssue(0).getName().toStringUtf8();

  // BUG: secondName == "b_asset" again (duplicate), because "a_asset" now occupies index 0
  // and "b_asset" shifted to index 1, instead of "c_asset" as the client expected.
  assertEquals("b_asset", secondName); // demonstrates duplication across the two calls
}
```
Expected assertion for correct behavior: enumerating pages 0..N with any interleaved issuance should never re-return an asset already delivered in an earlier page nor skip an asset present in the pre-interleaving snapshot; the PoC shows the current implementation violates this invariant.

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
