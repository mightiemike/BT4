### Title
Unbounded full-column-family scan and sort per paginated asset-issue query causes CPU/latency DoS - (File: chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java)

### Summary
`AssetIssueStore.getAssetIssuesPaginated(long, long)` ignores the requested `offset`/`limit` when reading data: it calls `getAllAssetIssues()`, which uses `Streams.stream(iterator())` to pull **every** entry out of the `asset-issue` RocksDB column family into memory, before sorting the full list and finally slicing out the requested page. `ASSET_ISSUE_COUNT_LIMIT_MAX` only caps the returned page size, not the amount of work done to produce it.

### Finding Description
The pagination path is: `RpcApiService.WalletSolidityApi.getPaginatedAssetIssueList` / `GetPaginatedAssetIssueListServlet` → `Wallet.getAssetIssueList(offset, limit)` → `AssetIssueStore.getAssetIssuesPaginated(offset, limit)`. [1](#0-0) 

`getAssetIssuesPaginated(long, long)` unconditionally calls `getAllAssetIssues()` first, which does `Streams.stream(iterator()).map(Entry::getValue).collect(Collectors.toList())` — a full traversal of the underlying RocksDB iterator (`RockStoreIterator.hasNext()/next()`, backed by `dbIterator.seekToFirst()`/`dbIterator.next()`), deserializing every `AssetIssueCapsule` in the store into memory. Only after this full materialization does the private overload sort the entire list by name/order and take a `subList(offset, end)`. [2](#0-1) 

There is no cap on the total number of asset-issue entries scanned — `ASSET_ISSUE_COUNT_LIMIT_MAX = 1000` in `Commons.java` only bounds the returned `limit`, not the size of `assetIssueList` fetched from the DB. So regardless of the caller-supplied `offset=0, limit=1`, the server does O(n) DB reads and capsule deserializations plus an O(n log n) sort where n = total number of asset issues ever created on chain, for every single request.

`getPaginatedAssetIssueList` at the gRPC layer performs no additional validation of `offset`/`limit` before forwarding to `Wallet.getAssetIssueList`: [3](#0-2) 

The HTTP servlet (`GetPaginatedAssetIssueListServlet`) extends `RateLimiterServlet`, which provides some request-rate limiting, but does not bound the cost of each individual request — repeated cheap requests at the allowed rate still each perform the full O(n) scan/sort. [4](#0-3) 

Growing `n` requires an attacker to broadcast `AssetIssueContract`-creating transactions from funded accounts — an unprivileged action requiring only TRX for the (chain-configured) asset-issue fee and bandwidth/energy, which I was unable to fully verify the exact fee amount for in this codebase snapshot (references exist in `DynamicPropertiesStore.java`/`AssetIssueActuator.java` but I could not confirm the current default fee value or whether any additional creation-rate throttle applies at the actuator level).

### Impact Explanation
This matches TRON's "DoS via RPC-API" bounty class: an attacker can force the node to perform O(n) DB reads, O(n) heap allocations, and O(n log n) CPU-bound sorting on a hot RPC/HTTP path per request, independent of the tiny `limit` requested. As `n` (total on-chain asset issues) grows — which the attacker directly controls by creating more assets — every call to `getPaginatedAssetIssueList`/`getAssetIssueList` degrades linearly, enabling sustained latency amplification and potential OOM/thread-starvation on the RPC-API service if repeated concurrently, without requiring any privileged role.

### Likelihood Explanation
Preconditions are low: any funded account can broadcast `AssetIssueContract` creation transactions to grow `n`; only ordinary transaction fees/asset-issue fee (not architecturally prevented) are needed, and no code path enforces a global cap on the number of asset issues in the store. Once `n` is large, exploitation is trivial and repeatable — merely calling `getPaginatedAssetIssueList(offset=0, limit=1)` repeatedly reproduces the full-scan cost every time, and the HTTP `RateLimiterServlet` only throttles request frequency, not per-request cost. The main uncertainty is the exact asset-issue creation fee/cost economics, which bounds how expensive it is for the attacker to inflate `n`, and I could not fully confirm current default values from the available index.

### Recommendation
Avoid loading and sorting the entire asset-issue column family for a paginated request. Options:
1. Maintain a persistently sorted index (e.g., a secondary key ordered by name/order) that can be seeked directly to the requested `offset`, so `RockStoreIterator.seek()` can skip to the needed range instead of materializing all entries.
2. Alternatively, cache the sorted `List<AssetIssueCapsule>` (invalidated on asset-issue creation) so repeated pagination calls don't re-scan/re-sort on every request.
3. Enforce a hard cap on the total number of asset issues that can exist (already partially expressed via `ASSET_ISSUE_COUNT_LIMIT_MAX`, but that constant only limits the page size returned, not store growth) or bound per-request work independent of total store size.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/store/AssetIssueStorePaginationDoSTest.java
@Test
public void testPaginationScalesWithStoreSizeNotLimit() {
  AssetIssueStore store = ...; // obtain from ChainBaseManager in test context

  // Populate N synthetic AssetIssueCapsule entries directly into the store
  int n = 200_000;
  for (int i = 0; i < n; i++) {
    AssetIssueContract contract = AssetIssueContract.newBuilder()
        .setName(ByteString.copyFromUtf8("TOKEN" + i))
        .setId(String.valueOf(i))
        .build();
    store.put(ByteArray.fromString(String.valueOf(i)), new AssetIssueCapsule(contract));
  }

  long start = System.nanoTime();
  List<AssetIssueCapsule> page = store.getAssetIssuesPaginated(0, 1); // caller only wants 1 result
  long elapsedMs = (System.nanoTime() - start) / 1_000_000;

  // Expected (buggy) behavior: elapsedMs scales roughly linearly with n
  // (full scan + full sort of n entries), not O(1)/O(limit).
  System.out.println("Elapsed for offset=0,limit=1 with n=" + n + ": " + elapsedMs + "ms");
  assertEquals(1, page.size());
  // A fix should keep elapsedMs roughly constant regardless of n.
}
```
Request-level reproduction: after broadcasting a large number of `AssetIssueContract`-creation transactions from funded accounts, repeatedly send `getPaginatedAssetIssueList(offset=0, limit=1)` via gRPC (`WalletSolidityApi`) or HTTP (`/walletsolidity/getpaginatedassetissuelist?offset=0&limit=1`) and observe that response latency and node CPU/heap usage scale with the total number of asset issues on chain rather than with `limit`.

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

**File:** chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java (L34-84)
```java
  @Override
  public boolean hasNext() {
    if (close.get()) {
      return false;
    }
    boolean hasNext = false;
    // true is first item
    try {
      if (first) {
        dbIterator.seekToFirst();
        first = false;
      }
      if (!(hasNext = dbIterator.isValid())) { // false is last item
        close();
      }
    } catch (Exception e) {
      logger.error(e.getMessage(), e);
      try {
        close();
      } catch (Exception e1) {
        logger.error(e1.getMessage(), e1);
      }
    }
    return hasNext;
  }

  @Override
  public Entry<byte[], byte[]> next() {
    if (close.get()) {
      throw new NoSuchElementException();
    }
    byte[] key = dbIterator.key();
    byte[] value = dbIterator.value();
    dbIterator.next();
    return new Entry<byte[], byte[]>() {
      @Override
      public byte[] getKey() {
        return key;
      }

      @Override
      public byte[] getValue() {
        return value;
      }

      @Override
      public byte[] setValue(byte[] value) {
        throw new UnsupportedOperationException();
      }
    };
  }
```

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L1848-1852)
```java
    public void getPaginatedAssetIssueList(PaginatedMessage request,
        StreamObserver<AssetIssueList> responseObserver) {
      responseObserver.onNext(wallet.getAssetIssueList(request.getOffset(), request.getLimit()));
      responseObserver.onCompleted();
    }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java (L20-52)
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
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
  }
```
