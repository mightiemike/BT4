### Title
Unbounded, unpaginated store iteration in `Wallet.getExchangeList()` / `getProposalList()` / `getAssetIssueList()` / `getWitnessList()` exposed via anonymous RPC/HTTP causes response-size/latency DoS as chain state grows - ([File: framework/src/main/java/org/tron/core/Wallet.java])

### Summary
`Wallet` exposes several unpaginated "list all" query methods that iterate the *entire* underlying LevelDB/RocksDB store and marshal every record into a single protobuf response, reachable anonymously via gRPC (`ListExchanges`, `ListProposals`, `GetAssetIssueList`, `ListWitnesses`) and their HTTP equivalents (`/wallet/listexchanges`, `/wallet/listproposals`, `/wallet/getassetissuelist`). This is the same bug class as the Sherlock report on `BondAggregator.liveMarketsBy`: a public read entrypoint that loops over an ever-growing, attacker/user-influenced collection with no bound, so the cost of a single call grows unboundedly with chain history and will eventually degrade or fail as the collection grows, even though paginated alternatives were later added for some (but not all) of these lists.

### Finding Description
`AssetIssueStore.getAllAssetIssues()` streams the full asset-issue store into a `List` with no limit [1](#0-0) , and `Wallet.getAssetIssueList()` calls it and appends every result to a single `AssetIssueList` builder [2](#0-1) . The same pattern exists for `getAssetIssueByAccount` and `getAssetIssueListByName`, both of which pull `getAllAssetIssues()` and then filter in-memory rather than doing a bounded lookup [3](#0-2) [4](#0-3) .

`ProposalStore.getAllProposals()` similarly streams the entire proposal store, sorts it, and returns it unbounded [5](#0-4) , and `Wallet.getProposalList()` builds a full `ProposalList` from it [6](#0-5) .

These unbounded accessors are wired directly to anonymous, unauthenticated entrypoints:
- gRPC `ListExchanges`, `ListProposals`, `GetAssetIssueList`, `ListWitnesses` in `api.proto` [7](#0-6) 
- `RpcApiService.listProposals` directly forwards to `wallet.getProposalList()` with no size checks [8](#0-7) 
- The equivalent `ListProposalsServlet` / `GetAssetIssueListServlet` HTTP endpoints have no auth and no bound, just forwarding straight to the same unbounded `Wallet` methods [9](#0-8) [10](#0-9) 

Notably, java-tron's developers already recognized this exact problem for these same lists and added paginated variants — `getPaginatedProposalList(offset, limit)` (capped by `PROPOSAL_COUNT_LIMIT_MAX`) [11](#0-10) , `getPaginatedExchangeList` (capped by `EXCHANGE_COUNT_LIMIT_MAX`) [12](#0-11) , and `getAssetIssuesPaginated` (capped by `ASSET_ISSUE_COUNT_LIMIT_MAX`) [13](#0-12) . This mirrors precisely the remediation applied to `BondAggregator` in the referenced report (adding start/stop indices for pagination). However, the *original unbounded* methods (`ListProposals`, `ListExchanges`, `GetAssetIssueList`, `getAssetIssueByAccount`, `getAssetIssueListByName`) were kept in the API surface for backward compatibility and remain fully unbounded, so the underlying vulnerability class was only mitigated, not eliminated, for callers still using the legacy unpaginated endpoints.

### Impact Explanation
As the number of proposals, exchanges, asset issues, or witnesses grows over the life of the chain (proposals/exchanges/assets can be created by any account via ordinary transactions — `ProposalCreateContract`, `ExchangeCreateContract`, `AssetIssueContract`), a single call to these unpaginated RPC/HTTP methods does proportionally more DB iteration, protobuf serialization, and memory allocation. Eventually this can: (1) produce a response exceeding gRPC/HTTP message size limits, causing the call to fail for every caller; (2) consume excessive heap/CPU on the node servicing the request, degrading service for all other RPC/HTTP clients on that node (DoS via RPC-API); and (3) since these are unauthenticated, anonymous callers can trigger this cost repeatedly at will. This does not affect consensus, but it is a legitimate node-level DoS/availability defect on the public query API, matching the "impact" class validated by java-tron's own fix (adding pagination) for the sibling methods.

### Likelihood Explanation
Likelihood grows monotonically and passively with normal chain usage (more proposals/exchanges/assets/witnesses are created over time by ordinary users), requiring no attacker privilege beyond issuing a normal anonymous API call to `/wallet/listproposals`, `/wallet/listexchanges`, `/wallet/getassetissuelist`, or the corresponding gRPC methods. No malicious peer, node, or key compromise is required — this is directly analogous to `BondAggregator.liveMarketsBy` being callable by anyone and degrading purely due to market count. Given that java-tron already had to add pagination for these same three lists (proposals, exchanges, asset issues) for this exact reason, the residual unpaginated legacy methods remain reachable and exhibit the identical growth-driven degradation.

### Recommendation
Deprecate and eventually remove (or internally cap/paginate) the unbounded `ListProposals`, `ListExchanges`, `GetAssetIssueList`, `getAssetIssueByAccount`, and `getAssetIssueListByName` code paths, redirecting all callers to the paginated variants (`GetPaginatedProposalList`, `GetPaginatedExchangeList`, `GetPaginatedAssetIssueList`). At minimum, enforce a hard maximum result count/response size in the unbounded methods (similar to `PROPOSAL_COUNT_LIMIT_MAX`/`EXCHANGE_COUNT_LIMIT_MAX`/`ASSET_ISSUE_COUNT_LIMIT_MAX`) so a single request cannot iterate or serialize an unbounded number of records, and apply the same treatment to `getAssetIssueByAccount`/`getAssetIssueListByName`, which currently have no paginated equivalent at all.

### Proof of Concept
1. Repeatedly submit `AssetIssueContract` / `ProposalCreateContract` / `ExchangeCreateContract` transactions from many accounts over time (all normal, unprivileged operations) to grow `AssetIssueStore` / `ProposalStore` / `ExchangeStore` to a large size.
2. Call the anonymous, unauthenticated endpoints `GET /wallet/getassetissuelist`, `GET /wallet/listproposals`, `GET /wallet/listexchanges` (or the gRPC equivalents `GetAssetIssueList`, `ListProposals`, `ListExchanges`).
3. Observe that response latency, memory usage, and serialized payload size scale linearly with the total number of stored records, with no client-controllable bound — exactly the pattern flagged in `BondAggregator.liveMarketsBy`, and the reason java-tron's own maintainers had to introduce `getPaginatedProposalList`/`getPaginatedExchangeList`/`getAssetIssuesPaginated` as bounded replacements [11](#0-10) .

### Citations

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

**File:** framework/src/main/java/org/tron/core/Wallet.java (L874-881)
```java
  public ProposalList getProposalList() {
    ProposalList.Builder builder = ProposalList.newBuilder();
    List<ProposalCapsule> proposalCapsuleList =
        chainBaseManager.getProposalStore().getAllProposals();
    proposalCapsuleList
        .forEach(proposalCapsule -> builder.addProposals(proposalCapsule.getInstance()));
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

**File:** framework/src/main/java/org/tron/core/Wallet.java (L3307-3339)
```java
  public ExchangeList getPaginatedExchangeList(long offset, long limit) {
    if (limit < 0 || offset < 0) {
      return null;
    }

    long latestExchangeNum = chainBaseManager.getDynamicPropertiesStore()
        .getLatestExchangeNum();
    if (latestExchangeNum <= offset) {
      return null;
    }
    limit =
        limit > EXCHANGE_COUNT_LIMIT_MAX ? EXCHANGE_COUNT_LIMIT_MAX : limit;
    long end = offset + limit;
    end = end > latestExchangeNum ? latestExchangeNum : end;

    ExchangeList.Builder builder = ExchangeList.newBuilder();
    ImmutableList<Long> rangeList = ContiguousSet
        .create(Range.openClosed(offset, end), DiscreteDomain.longs())
        .asList();
    rangeList.stream().map(ExchangeCapsule::calculateDbKey).map(key -> {
      try {
        return getExchangeStoreFinal(chainBaseManager.getDynamicPropertiesStore(),
            chainBaseManager.getExchangeStore(),
            chainBaseManager.getExchangeV2Store()).get(key);
      } catch (Exception ex) {
        return null;
      }
    }).filter(Objects::nonNull)
        .forEach(exchangeCapsule -> builder
            .addExchanges(exchangeCapsule.getInstance()));
    return builder.build();

  }
```

**File:** chainbase/src/main/java/org/tron/core/store/ProposalStore.java (L29-39)
```java
  /**
   * get all proposals.
   */
  public List<ProposalCapsule> getAllProposals() {
    return Streams.stream(iterator())
        .map(Map.Entry::getValue)
        .sorted(
            (ProposalCapsule a, ProposalCapsule b) -> a.getCreateTime() <= b.getCreateTime() ? 1
                : -1)
        .collect(Collectors.toList());
  }
```

**File:** protocol/src/main/protos/api/api.proto (L310-330)
```text
  rpc ListProposals (EmptyMessage) returns (ProposalList) {
  };
  rpc GetPaginatedProposalList (PaginatedMessage) returns (ProposalList) {
  }
  rpc GetProposalById (BytesMessage) returns (Proposal) {
  };

  rpc ListExchanges (EmptyMessage) returns (ExchangeList) {
  };
  rpc GetPaginatedExchangeList (PaginatedMessage) returns (ExchangeList) {
  }
  rpc GetExchangeById (BytesMessage) returns (Exchange) {
  };

  rpc GetChainParameters (EmptyMessage) returns (ChainParameters) {
  };

  rpc GetAssetIssueList (EmptyMessage) returns (AssetIssueList) {
  }
  rpc GetPaginatedAssetIssueList (PaginatedMessage) returns (AssetIssueList) {
  }
```

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L1888-1893)
```java
    @Override
    public void listProposals(EmptyMessage request,
        StreamObserver<ProposalList> responseObserver) {
      responseObserver.onNext(wallet.getProposalList());
      responseObserver.onCompleted();
    }
```

**File:** framework/src/main/java/org/tron/core/services/http/ListProposalsServlet.java (L18-31)
```java
  @Override
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      ProposalList reply = wallet.getProposalList();
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
