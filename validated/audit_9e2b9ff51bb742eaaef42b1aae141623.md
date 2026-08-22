### Title
Unbounded iteration over attacker-growable Exchange/AssetIssue stores in unpaginated RPC/HTTP APIs - (File: framework/src/main/java/org/tron/core/Wallet.java)

### Summary
`Wallet.getExchangeList()` and `Wallet.getAssetIssueList()` iterate over the *entire* `ExchangeStore`/`AssetIssueStore` with no upper bound, and are exposed through unauthenticated gRPC (`ListExchanges`, `GetAssetIssueList`) and HTTP (`/wallet/listexchanges`, `/wallet/getassetissuelist`) endpoints. Both stores grow unboundedly as a direct result of ordinary, unprivileged transactions (`ExchangeCreateContract`, `AssetIssueContract`), matching the same bug class described in the external report: a `for`/stream loop bounded by a collection whose size has no enforced maximum, exposed to callers who can inflate that collection.

### Finding Description
`ExchangeStore.getAllExchanges()` streams and sorts every entry in the store with no cap: [1](#0-0) 

`Wallet.getExchangeList()` calls this unbounded method directly and is reachable from the anonymous `ListExchanges` RPC and `/wallet/listexchanges` HTTP endpoint: [2](#0-1) [3](#0-2) [4](#0-3) 

The same pattern exists for `AssetIssueStore.getAllAssetIssues()` / `Wallet.getAssetIssueList()`, exposed via `GetAssetIssueList` RPC and `/wallet/getassetissuelist`: [5](#0-4) [6](#0-5) [7](#0-6) 

Both stores are populated by ordinary, unprivileged transactions that any account can broadcast (an `ExchangeCreateContract` costs a fixed 1024 TRX fee, and `AssetIssueContract` a similar fixed fee — see `JsonRpcApiUtil.getTransactionAmount`), with no protocol-level cap on the *total number* of exchanges or assets that can exist on-chain: [8](#0-7) 

This is the same class of defect as the external report: a loop's iteration count is governed by an attacker/user-influenced collection with no documented or enforced maximum. The codebase already demonstrates awareness of this bug class and has fixed it for the analogous witness/vote/permission/proposal/unfreeze loops — e.g. `MAX_VOTE_NUMBER` bounding `VoteWitnessActuator`, `UNFREEZE_MAX_TIMES` bounding unfreeze list growth, `permission.getKeysCount() > getTotalSignNum()` bounding permission key loops, and paginated variants (`getPaginatedProposalList`, `getPaginatedExchangeList`, `getAssetIssueList(offset, limit)`) added alongside the legacy unbounded calls. However, the legacy unbounded `getExchangeList()` / `getAssetIssueList()` entry points remain live and unauthenticated, so the fix is incomplete: nothing prevents the underlying store from growing arbitrarily large, and nothing stops clients from invoking the unpaginated variant.

### Impact Explanation
As the number of exchanges/assets created on-chain grows (which any unprivileged account can drive up over time at a bounded but nonzero cost), calls to `ListExchanges`/`GetAssetIssueList` become increasingly expensive (O(n) iteration + sort for exchanges), directly consumable by any anonymous RPC/HTTP client with no pagination limit. Because these RPCs run on the full/solidity/PBFT node process, sustained or repeated calls degrade node responsiveness for legitimate API consumers — a denial-of-service vector against the public RPC-API surface, consistent with the "DoS via RPC-API" acceptance criterion.

### Likelihood Explanation
Likelihood is moderate: an attacker needs to (1) pay the fixed fee to create many exchanges/assets over time to inflate the store, and (2) repeatedly call the unpaginated, unauthenticated list endpoints. Both actions require no special privilege and are already exposed by default on any node running the HTTP/gRPC API services (`FullNodeHttpApiService`, `RpcApiService`), which are commonly enabled on public-facing full nodes.

### Recommendation
- Enforce a global, on-chain cap on the total number of exchanges/asset issues (similar to `MAX_VOTE_NUMBER`, `UNFREEZE_MAX_TIMES`), or scale the creation fee so growth cannot be driven unboundedly cheaply.
- Deprecate/remove the unpaginated `getExchangeList()`/`getAssetIssueList()` code paths (and their RPC/HTTP handlers) in favor of the already-implemented paginated variants (`getPaginatedExchangeList`, `getAssetIssueList(offset, limit)`), or internally cap the number of entries returned/iterated even when the unpaginated API is invoked.
- Apply the same rate limiting/pagination discipline already used for `getPaginatedNowWitnessList` (`WITNESS_COUNT_LIMIT_MAX`) and `getPaginatedProposalList` (`PROPOSAL_COUNT_LIMIT_MAX`) consistently to every store-wide listing endpoint.

### Proof of Concept
1. From an unprivileged account, broadcast repeated `ExchangeCreateContract` (or `AssetIssueContract`) transactions, each paying the fixed fee, to grow `ExchangeStore`/`AssetIssueStore` to a very large size over time.
2. Call the anonymous gRPC `ListExchanges`/`GetAssetIssueList` (or HTTP `/wallet/listexchanges`, `/wallet/getassetissuelist`) endpoint repeatedly.
3. Observe that each call performs an unbounded full-store scan/sort (`ExchangeStore.getAllExchanges()` / `AssetIssueStore.getAllAssetIssues()`), with response time and node resource usage scaling linearly with the attacker-inflated store size, degrading service for all other API clients.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/ExchangeStore.java (L28-38)
```java
  /**
   * get all exchanges.
   */
  public List<ExchangeCapsule> getAllExchanges() {
    return Streams.stream(iterator())
        .map(Map.Entry::getValue)
        .sorted(
            (ExchangeCapsule a, ExchangeCapsule b) -> a.getCreateTime() <= b.getCreateTime() ? 1
                : -1)
        .collect(Collectors.toList());
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1066-1076)
```java
  public ExchangeList getExchangeList() {
    ExchangeList.Builder builder = ExchangeList.newBuilder();
    List<ExchangeCapsule> exchangeCapsuleList =
        getExchangeStoreFinal(chainBaseManager.getDynamicPropertiesStore(),
            chainBaseManager.getExchangeStore(),
            chainBaseManager.getExchangeV2Store()).getAllExchanges();

    exchangeCapsuleList
        .forEach(exchangeCapsule -> builder.addExchanges(exchangeCapsule.getInstance()));
    return builder.build();
  }
```

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

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L603-608)
```java
    @Override
    public void listExchanges(EmptyMessage request,
        StreamObserver<ExchangeList> responseObserver) {
      responseObserver.onNext(wallet.getExchangeList());
      responseObserver.onCompleted();
    }
```

**File:** framework/src/main/java/org/tron/core/services/http/ListExchangesServlet.java (L18-25)
```java
  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      response.getWriter().println(JsonFormat.printToString(wallet.getExchangeList(), visible));
    } catch (Exception e) {
      Util.processError(e, response);
    }
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

**File:** framework/src/main/java/org/tron/core/services/http/GetAssetIssueListServlet.java (L18-31)
```java
  @Override
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      AssetIssueList reply = wallet.getAssetIssueList();
      if (reply != null) {
        response.getWriter().println(JsonFormat.printToString(reply, visible));
      } else {
        response.getWriter().println("{}");
      }
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java (L270-273)
```java
        case AssetIssueContract:
        case ExchangeCreateContract:
          amount = 1024_000_000L;
          break;
```
