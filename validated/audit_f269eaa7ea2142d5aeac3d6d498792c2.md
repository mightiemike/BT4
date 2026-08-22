### Title
Unbounded full-table scan in `Wallet.getAssetIssueByAccount` causes RPC-API CPU DoS - (File: framework/src/main/java/org/tron/core/Wallet.java)

### Summary
`Wallet.getAssetIssueByAccount` loads the entire asset-issue table via `getAllAssetIssues()` and filters in Java for every call, instead of doing an indexed lookup keyed by owner address. Because this endpoint is reachable unauthenticated via gRPC (`getAssetIssueByAccount`) and HTTP (`/wallet/getassetissuebyaccount`), an attacker can force O(total assets) work on every request regardless of the caller's actual token holdings (0 or 1 in practice).

### Finding Description
The implementation is: [1](#0-0) 
It calls `AssetIssueStore.getAllAssetIssues()`, which iterates the entire underlying RocksDB column via `Streams.stream(iterator())` and materializes every `AssetIssueCapsule` into a `List`: [2](#0-1) 
This is then filtered client-side (`.filter(assetIssueCapsule -> assetIssueCapsule.getOwnerAddress().equals(accountAddress))`), meaning the DB store has no owner-indexed lookup — the on-disk key for an asset is derived from the asset name (`createDbKey()`), not from the owner address, so there is no way to do a direct/indexed fetch by owner.

The call is reachable from two unauthenticated external surfaces:
- gRPC `Wallet.getAssetIssueByAccount`: [3](#0-2) 
- HTTP `/wallet/getassetissuebyaccount`: [4](#0-3) 

The only protection in front of the HTTP servlet is a generic QPS-based `RateLimiterServlet`, which throttles request *rate* but does not account for the *cost* of each request — it does not scale the allowed rate down as the asset table grows, nor does it cap per-request work. The gRPC path has no rate limiting visible in the reviewed code path beyond the same class of QPS interceptors. There is no permission, signature, or fee check on this read-only query path (it is a pure getter, not a transaction), so none of the standard transaction-level defenses (`validateSignature`, actuator `validate()`, energy/bandwidth accounting) apply.

Growing the table is cheap and permissionless: any funded account can broadcast an `AssetIssueContract` transaction via `AssetIssueActuator`, paying only `dynamicStore.getAssetIssueFee()` [5](#0-4) . Note that each account may issue only one asset (`"An account can only issue one asset"` check) [6](#0-5) , so scaling the table requires creating additional funded accounts, but account creation itself is cheap and permissionless, so this is not a meaningful barrier to steadily growing the table over time.

The `getAssetIssueList()` variant suffers the identical full-scan pattern and the same root cause exists across both endpoints [7](#0-6) .

### Impact Explanation
This is an RPC-API CPU/latency DoS: the cost of `GetAssetIssueByAccount` scales linearly with the total number of asset issues on-chain rather than with the target account's actual holdings. As the asset-issue table grows (whether organically or via attacker-seeded entries), every call to this endpoint gets more expensive, and a modest flood of concurrent requests against arbitrary addresses can saturate node CPU/thread pool serving RPC/HTTP requests, degrading availability of the queried FullNode/SolidityNode for legitimate API consumers. This matches the "DoS via RPC-API" bounty impact class — it does not affect consensus or lead to loss of funds, and is a resource-exhaustion/availability issue on the API-serving node rather than the P2P/consensus layer.

### Likelihood Explanation
- No privileged role or key leak is required — any anonymous RPC/HTTP client can call this endpoint with arbitrary addresses.
- Growing the underlying table is possible for any funded account paying the standard `AssetIssueContract` fee (one per account), and additional accounts are cheap to create, so the table can be grown to a large size over time.
- Existing QPS-based rate limiters (`RateLimiterServlet`, gRPC QPS interceptors) do not mitigate this because they cap request rate, not per-request cost; an attacker can stay under the QPS limit while each permitted request still forces a full-table scan.
- The vulnerability is fully repeatable and its cost grows monotonically as more `AssetIssueContract`s accumulate on mainnet over time (this store is append-mostly and never shrinks), so this is a genuine and worsening condition, not a one-off issue.

### Recommendation
Add an owner-address-indexed secondary lookup (e.g., a `Map<ownerAddress, List<assetKey>>` cache maintained incrementally in `AssetIssueStore`/`AssetIssueV2Store`, or a dedicated RocksDB column keyed by owner address) so `getAssetIssueByAccount` performs an O(result-size) lookup instead of an O(total-assets) scan. As a stop-gap, apply per-request cost-aware or asset-count-aware rate limiting rather than pure QPS limiting on this endpoint, and consider capping/paginating results similarly to `getAssetIssueList(offset, limit)`.

### Proof of Concept
```java
// JUnit-style benchmark demonstrating O(total assets) scaling
@Test
public void testGetAssetIssueByAccountScaling() {
  // Baseline: measure latency with N assets issued (from N distinct funded accounts)
  for (int n : new int[]{100, 1000, 10000, 50000}) {
    issueDistinctAssets(n); // broadcasts N AssetIssueContract txns from N funded accounts
    long start = System.nanoTime();
    wallet.getAssetIssueByAccount(ByteString.copyFrom(randomAddressNotInTable()));
    long elapsed = System.nanoTime() - start;
    System.out.println("n=" + n + " latency(ns)=" + elapsed);
  }
  // Expected: latency grows ~linearly with n even though the queried address
  // owns zero assets, confirming full-table scan cost regardless of filtered result size.
}
```
Equivalent request-level PoC: repeatedly call `GET /wallet/getassetissuebyaccount?address=<random_address>` (or the gRPC `getAssetIssueByAccount` RPC) against a node with a large `asset-issue` store and observe response latency scaling with total asset count rather than with the number of assets actually owned by `<random_address>`.

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1530-1544)
```java
  public AssetIssueList getAssetIssueList() {
    BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
    AssetIssueList.Builder builder = AssetIssueList.newBuilder();

    getAssetIssueStoreFinal(chainBaseManager.getDynamicPropertiesStore(),
        chainBaseManager.getAssetIssueStore(),
        chainBaseManager.getAssetIssueV2Store()).getAllAssetIssues()
        .forEach(
            issueCapsule -> {
              processor.updateUsage(issueCapsule);
              builder.addAssetIssue(issueCapsule.getInstance());
            });

    return builder.build();
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1568-1589)
```java
  public AssetIssueList getAssetIssueByAccount(ByteString accountAddress) {
    if (accountAddress == null || accountAddress.isEmpty()) {
      return null;
    }

    List<AssetIssueCapsule> assetIssueCapsuleList =
        getAssetIssueStoreFinal(chainBaseManager.getDynamicPropertiesStore(),
            chainBaseManager.getAssetIssueStore(),
            chainBaseManager.getAssetIssueV2Store()).getAllAssetIssues();

    BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
    AssetIssueList.Builder builder = AssetIssueList.newBuilder();
    assetIssueCapsuleList.stream()
        .filter(assetIssueCapsule -> assetIssueCapsule.getOwnerAddress().equals(accountAddress))
        .forEach(
            issueCapsule -> {
              processor.updateUsage(issueCapsule);
              builder.addAssetIssue(issueCapsule.getInstance());
            });

    return builder.build();
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

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L1549-1560)
```java
    @Override
    public void getAssetIssueByAccount(Account request,
        StreamObserver<AssetIssueList> responseObserver) {
      ByteString fromBs = request.getAddress();

      if (fromBs != null) {
        responseObserver.onNext(wallet.getAssetIssueByAccount(fromBs));
      } else {
        responseObserver.onNext(null);
      }
      responseObserver.onCompleted();
    }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetAssetIssueByAccountServlet.java (L22-54)
```java
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      String address = request.getParameter("address");
      if (visible) {
        address = Util.getHexAddress(address);
      }
      fillResponse(visible, ByteString.copyFrom(ByteArray.fromHexString(address)), response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      Account.Builder build = Account.newBuilder();
      JsonFormat.merge(params.getParams(), build, params.isVisible());
      fillResponse(params.isVisible(), build.getAddress(), response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  private void fillResponse(boolean visible, ByteString address, HttpServletResponse response)
      throws Exception {
    AssetIssueList reply = wallet.getAssetIssueByAccount(address);
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L287-289)
```java
    if (!accountCapsule.getAssetIssuedName().isEmpty()) {
      throw new ContractValidateException("An account can only issue one asset");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L302-305)
```java
  @Override
  public long calcFee() {
    return chainBaseManager.getDynamicPropertiesStore().getAssetIssueFee();
  }
```
