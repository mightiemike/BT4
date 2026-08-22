### Title
Unbounded full-store iteration in unpaginated wallet list RPCs (`ListWitnesses`, `ListExchanges`, `GetAssetIssueList`) enables anonymous RPC/HTTP DoS as attacker-created records grow - (File: `framework/src/main/java/org/tron/core/Wallet.java`)

### Summary
The Sherlock finding describes `BondAggregator.liveMarketsBy` looping over an ever-growing on-chain array with per-item external calls, eventually exceeding the block gas limit for a `view` call and permanently reverting/DoSing the getter. The analogous bug class in java-tron is present in several unpaginated wallet query methods that fully materialize a store into memory on every call: `Wallet.getWitnessList()`, `Wallet.getExchangeList()`, and `Wallet.getAssetIssueList()` (no-arg overload). These are exposed via anonymous, unauthenticated gRPC/HTTP endpoints (`ListWitnesses`, `ListExchanges`, `GetAssetIssueList`), and the backing collections (`WitnessStore`, `ExchangeStore`, `AssetIssueStore`) grow via ordinary, permissionless broadcast transactions (`WitnessCreateContract`, `ExchangeCreateContract`, `AssetIssueContract`).

### Finding Description
`Wallet.getAssetIssueList()` calls `AssetIssueStore.getAllAssetIssues()`, which does `Streams.stream(iterator()).map(Entry::getValue).collect(Collectors.toList())` [1](#0-0)  — i.e. it materializes every asset issue record in the DB on each call, then `Wallet.getAssetIssueList()` runs `updateUsage` on every entry and rebuilds a protobuf list [2](#0-1) . `Wallet.getAssetIssueByName` and `getAssetIssueListByName` similarly call `getAllAssetIssues()` and filter client-side rather than doing a bounded lookup [3](#0-2) .

This is exposed anonymously through the `GetAssetIssueList` RPC/HTTP endpoint and its servlet, which perform no pagination and just forward to the wallet method [4](#0-3) , and the RPC handler `listExchanges`/`listWitnesses`/`GetAssetIssueList` in `RpcApiService` similarly call the unpaginated wallet getters directly [5](#0-4) .

Crucially, the size of these stores is attacker-controllable without any special privilege: any account can issue a new asset via `AssetIssueContract` or create a witness via `WitnessCreateContract` by broadcasting an ordinary signed transaction and paying the standard fee — these are not admin/witness-only actions. Repeated cheap broadcasts can grow `AssetIssueStore`/`WitnessStore`/`ExchangeStore` unboundedly, so each subsequent call to the unpaginated `ListWitnesses`, `ListExchanges`, and `GetAssetIssueList` endpoints does O(n) work (deserializing every record, running bandwidth-usage updates per asset, rebuilding potentially huge protobuf responses) with n growing linearly with attacker-submitted transactions.

The project's own remediation pattern for this exact bug class is visible: paginated counterparts were added for asset issues (`getAssetIssueList(offset, limit)` backed by `AssetIssueStore.getAssetIssuesPaginated`) [6](#0-5) , proposals (`getPaginatedProposalList`) [7](#0-6) , and witnesses (`GetPaginatedNowWitnessList`) [8](#0-7) . This mirrors exactly the fix applied to `BondAggregator` (adding start/stop indices for pagination), confirming that the maintainers recognize unbounded full-store iteration in query APIs as the underlying problem class — but the legacy unpaginated `ListWitnesses`, `ListExchanges`, and `GetAssetIssueList` (no-arg) endpoints remain reachable and unbounded.

### Impact Explanation
Unlike the Solidity case, there is no fixed block-gas-limit causing a hard revert; instead the impact is denial-of-service on the FullNode/SolidityNode RPC and HTTP services: each unpaginated list call performs unbounded CPU/memory work and returns an unbounded-size response as the underlying store grows. Since these endpoints are anonymous and unauthenticated, an attacker can cheaply inflate the store size (issuing many assets/witnesses/exchanges via ordinary transactions) and then repeatedly invoke the unpaginated list RPCs to consume node CPU/memory/bandwidth, degrading service for other legitimate API consumers (explorers, wallets, exchanges depending on `ListWitnesses`/`ListExchanges`/`GetAssetIssueList`). This is a resource-exhaustion / availability risk against the RPC-API surface, not a consensus or funds-safety issue.

### Likelihood Explanation
Likelihood is moderate: growing the underlying stores requires paying standard transaction fees (asset issuance fee, witness creation fee), which provides some cost friction, but these are ordinary permissionless operations available to any account, and there is no upper bound enforced on the number of assets/witnesses/exchanges that can exist. Over time, and especially with a determined or well-funded attacker, the store sizes can grow to make each unpaginated list call meaningfully expensive, and these endpoints have no argument-based cap unlike their paginated siblings.

### Recommendation
Deprecate/remove or hard-cap the unpaginated `ListWitnesses`, `ListExchanges`, and `GetAssetIssueList` (no-arg) endpoints and steer all API consumers to the already-existing paginated equivalents (`GetPaginatedNowWitnessList`, `GetPaginatedExchangeList`, `GetPaginatedAssetIssueList`), consistent with how `GetPaginatedProposalList` was introduced. If backward compatibility must be preserved, impose a maximum result size/iteration cap inside `Wallet.getWitnessList()`, `Wallet.getExchangeList()`, and `Wallet.getAssetIssueList()` themselves (not just in the paginated overloads) so the unbounded variants cannot be used to force O(n) work with unbounded n.

### Proof of Concept
1. From any funded account, broadcast repeated `AssetIssueContract` (or `WitnessCreateContract` / `ExchangeCreateContract`) transactions to grow `AssetIssueStore`/`WitnessStore`/`ExchangeStore` to a large size.
2. Call the anonymous RPC/HTTP endpoint `GetAssetIssueList` (or `ListWitnesses`/`ListExchanges`), which invokes `Wallet.getAssetIssueList()` → `AssetIssueStore.getAllAssetIssues()` [1](#0-0) , causing full deserialization/iteration of the entire store and construction of an unbounded protobuf response on every single request.
3. Repeat calls to amplify CPU/memory/bandwidth consumption on the target FullNode, degrading availability of the RPC/HTTP service for other users, analogous to how `liveMarketsBy` in the original report becomes unusable/DoS-prone as `marketCounter` grows.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java (L34-38)
```java
  public List<AssetIssueCapsule> getAllAssetIssues() {
    return Streams.stream(iterator())
        .map(Entry::getValue)
        .collect(Collectors.toList());
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java (L40-63)
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

  public List<AssetIssueCapsule> getAssetIssuesPaginated(long offset, long limit) {
    return getAssetIssuesPaginated(getAllAssetIssues(), offset, limit);
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

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1774-1795)
```java
  public AssetIssueList getAssetIssueListByName(ByteString assetName) {
    if (assetName == null || assetName.isEmpty()) {
      return null;
    }

    List<AssetIssueCapsule> assetIssueCapsuleList =
        getAssetIssueStoreFinal(chainBaseManager.getDynamicPropertiesStore(),
            chainBaseManager.getAssetIssueStore(),
            chainBaseManager.getAssetIssueV2Store()).getAllAssetIssues();

    BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
    AssetIssueList.Builder builder = AssetIssueList.newBuilder();
    assetIssueCapsuleList.stream()
        .filter(assetIssueCapsule -> assetIssueCapsule.getName().equals(assetName))
        .forEach(
            issueCapsule -> {
              processor.updateUsage(issueCapsule);
              builder.addAssetIssue(issueCapsule.getInstance());
            });

    return builder.build();
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L3275-3305)
```java
  public ProposalList getPaginatedProposalList(long offset, long limit) {

    if (limit < 0 || offset < 0) {
      return null;
    }

    long latestProposalNum = chainBaseManager.getDynamicPropertiesStore()
        .getLatestProposalNum();
    if (latestProposalNum <= offset) {
      return null;
    }
    limit =
        limit > PROPOSAL_COUNT_LIMIT_MAX ? PROPOSAL_COUNT_LIMIT_MAX : limit;
    long end = offset + limit;
    end = end > latestProposalNum ? latestProposalNum : end;
    ProposalList.Builder builder = ProposalList.newBuilder();

    ImmutableList<Long> rangeList = ContiguousSet
        .create(Range.openClosed(offset, end), DiscreteDomain.longs())
        .asList();
    rangeList.stream().map(ProposalCapsule::calculateDbKey).map(key -> {
      try {
        return chainBaseManager.getProposalStore().get(key);
      } catch (Exception ex) {
        return null;
      }
    }).filter(Objects::nonNull)
        .forEach(proposalCapsule -> builder
            .addProposals(proposalCapsule.getInstance()));
    return builder.build();
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

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L1870-1893)
```java
    public void listWitnesses(EmptyMessage request,
        StreamObserver<WitnessList> responseObserver) {
      responseObserver.onNext(wallet.getWitnessList());
      responseObserver.onCompleted();
    }

    @Override
    public void getPaginatedNowWitnessList(PaginatedMessage request,
        StreamObserver<WitnessList> responseObserver) {
      try {
        responseObserver.onNext(
            wallet.getPaginatedNowWitnessList(request.getOffset(), request.getLimit()));
      } catch (MaintenanceUnavailableException e) {
        responseObserver.onError(getRunTimeException(e));
      }
      responseObserver.onCompleted();
    }

    @Override
    public void listProposals(EmptyMessage request,
        StreamObserver<ProposalList> responseObserver) {
      responseObserver.onNext(wallet.getProposalList());
      responseObserver.onCompleted();
    }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedNowWitnessListServlet.java (L21-51)
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
      GrpcAPI.PaginatedMessage.Builder build = GrpcAPI.PaginatedMessage.newBuilder();
      JsonFormat.merge(params.getParams(), build, params.isVisible());
      fillResponse(build.getOffset(), build.getLimit(), params.isVisible(), response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  private void fillResponse(long offset, long limit, boolean visible, HttpServletResponse response)
      throws IOException, MaintenanceUnavailableException {
    GrpcAPI.WitnessList reply = wallet.getPaginatedNowWitnessList(offset, limit);
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
  }
```
