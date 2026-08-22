Confirmed: `SolidityNodeHttpApiService.addServlet` registers `getMarketOrderListByPairServlet` and `getMarketPairListServlet` under `/walletsolidity/getmarketorderlistbypair` and `/walletsolidity/getmarketpairlist` (not `/wallet/...`), and its `addFilter` method only wires `httpApiAccessFilter`, never `LiteFnQueryHttpFilter`. `LiteFnQueryHttpFilter`'s static `filterPaths` set does include exactly these `/walletsolidity/...` paths, meaning the guard exists and targets these routes—but is simply never mounted onto this particular `HttpService`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Missing LiteFnQueryHttpFilter registration in SolidityNodeHttpApiService allows unbounded market-store iteration on lite nodes - (File: framework/src/main/java/org/tron/core/services/http/solidity/SolidityNodeHttpApiService.java)

### Summary
`SolidityNodeHttpApiService` registers `/walletsolidity/getmarketorderlistbypair` and `/walletsolidity/getmarketpairlist` servlets but its `addFilter` method only attaches `HttpApiAccessFilter`, never `LiteFnQueryHttpFilter`. Since `LiteFnQueryHttpFilter.filterPaths` explicitly lists these exact `/walletsolidity/...` paths as ones that should be blocked when `chainBaseManager.isLiteNode()` is true, the intended guard is simply not wired up for this specific `HttpService`, so on a lite solidity node the queries pass through unrestricted.

### Finding Description
`LiteFnQueryHttpFilter` is designed to block a set of history/market-query endpoints when the node is a lite node and `openHistoryQueryWhenLiteFN` is not set, by checking `chainBaseManager.isLiteNode() && !openHistoryQueryWhenLiteFN && filterPaths.contains(requestPath)` [5](#0-4) . The `filterPaths` static set explicitly contains `/walletsolidity/getmarketorderlistbypair` and `/walletsolidity/getmarketpairlist` [6](#0-5) , showing these exact paths were meant to be protected on lite nodes.

However, `SolidityNodeHttpApiService` — the `HttpService` implementation dedicated to solidity-node HTTP serving (`enable = !isFullNode() && Args.getInstance().isSolidityNodeHttpEnable()`) [7](#0-6)  — registers the two market servlets at these very paths [1](#0-0) , but its `addFilter(ServletContextHandler context)` override only adds `httpApiAccessFilter` and never adds `LiteFnQueryHttpFilter` [2](#0-1) . By contrast, `FullNodeHttpApiService`, `HttpApiOnSolidityService`, and `HttpApiOnPBFTService` all reference `LiteFnQueryHttpFilter` in their filter setup. This asymmetry means an unprivileged HTTP client hitting a lite node's dedicated solidity HTTP port can invoke these market servlets without being blocked, whereas the same requests are blocked on other HTTP services.

`isLiteNode()` is determined at startup from `ChainBaseManager`'s `@PostConstruct init()`, based on whether the lowest stored block number is greater than 1 (indicating pruned/lite history) [4](#0-3) . On such a node, `MarketOrderStore`/`MarketPairToPriceStore` full-range iteration triggered by wide/unbounded pair parameters in these servlets would still execute, incurring disk I/O proportional to stored order/pair records, for a free (no-fee, unauthenticated) HTTP GET.

### Impact Explanation
This is a DoS-via-RPC-API class issue: an unbounded, free HTTP query can force disproportionate store iteration/disk I/O on a lite node that is specifically supposed to have this class of expensive query blocked. Repeated invocation from an unprivileged client could degrade node responsiveness/availability without any fee, signature, or authorization check, since these are plain HTTP GET/POST endpoints with no transaction cost.

### Likelihood Explanation
Preconditions: node operator must run a lite-fullnode configuration with `solidityNodeHttp` enabled (a non-default but supported deployment mode for solidity nodes, e.g., an operator running `SolidityNodeHttpApiService` as their externally-facing lite query service). No attacker privilege beyond anonymous HTTP access is needed, and the request costs nothing (no transaction fee, no signature). It is trivially repeatable since it's a stateless GET with no rate limiting apparent in this service's filter chain beyond `httpApiAccessFilter` (which does IP/host-based access control, not query-cost limiting).

### Recommendation
Register `LiteFnQueryHttpFilter` in `SolidityNodeHttpApiService.addFilter` for the `/walletsolidity/*` path space (mirroring how `FullNodeHttpApiService`/`HttpApiOnSolidityService` wire it), or alternatively enforce the lite-node/`openHistoryQueryWhenLiteFN` check directly inside `GetMarketOrderListByPairServlet`/`GetMarketPairListServlet` so the guard is service-implementation independent rather than relying on filter registration per `HttpService`.

### Proof of Concept
```java
// JUnit-style test sketch against SolidityNodeHttpApiService's ServletContextHandler
// 1. Configure a ChainBaseManager fixture where lowestBlockNum > 1 so isLiteNode() == true
//    and CommonParameter.openHistoryQueryWhenLiteFN == false.
// 2. Populate MarketOrderStore/MarketPairToPriceStore with many pair/order records.
// 3. Start SolidityNodeHttpApiService (not FullNodeHttpApiService/HttpApiOnSolidityService).
// 4. Send: GET/POST http://<host>:<solidityHttpPort>/walletsolidity/getmarketpairlist
//    and http://<host>:<solidityHttpPort>/walletsolidity/getmarketorderlistbypair?...
// Expected (vulnerable) result: HTTP 200 with full market data returned, NOT the
// "this API is closed because this node is a lite fullnode" message that
// LiteFnQueryHttpFilter would emit if it were registered.
// Compare against the same request sent to FullNodeHttpApiService/HttpApiOnSolidityService
// under identical lite-node fixture: those correctly return the blocked-message response.
```

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/solidity/SolidityNodeHttpApiService.java (L169-174)
```java
  public SolidityNodeHttpApiService() {
    port = Args.getInstance().getSolidityHttpPort();
    enable = !isFullNode() && Args.getInstance().isSolidityNodeHttpEnable();
    contextPath = "/";
    maxRequestSize = Args.getInstance().getHttpMaxMessageSize();
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/solidity/SolidityNodeHttpApiService.java (L250-253)
```java
    context.addServlet(new ServletHolder(getMarketOrderListByPairServlet),
        "/walletsolidity/getmarketorderlistbypair");
    context.addServlet(new ServletHolder(getMarketPairListServlet),
        "/walletsolidity/getmarketpairlist");
```

**File:** framework/src/main/java/org/tron/core/services/http/solidity/SolidityNodeHttpApiService.java (L282-290)
```java
  @Override
  protected void addFilter(ServletContextHandler context) {
    // http access filter
    context.addFilter(new FilterHolder(httpApiAccessFilter), "/walletsolidity/*",
        EnumSet.allOf(DispatcherType.class));
    context.getServletHandler().getFilterMappings()[0]
        .setPathSpecs(new String[] {"/walletsolidity/*",
            "/wallet/getnodeinfo"});
  }
```

**File:** framework/src/main/java/org/tron/core/services/filter/LiteFnQueryHttpFilter.java (L59-80)
```java
    // base path: /walletsolidity
    filterPaths.add("/walletsolidity/getblockbyid");
    filterPaths.add("/walletsolidity/getblockbylatestnum");
    filterPaths.add("/walletsolidity/getblockbylimitnext");
    filterPaths.add("/walletsolidity/getblockbynum");
    filterPaths.add("/walletsolidity/getmerkletreevoucherinfo");
    filterPaths.add("/walletsolidity/gettransactionbyid");
    filterPaths.add("/walletsolidity/gettransactioncountbyblocknum");
    filterPaths.add("/walletsolidity/gettransactioninfobyid");
    filterPaths.add("/walletsolidity/isspend");
    filterPaths.add("/walletsolidity/scanandmarknotebyivk");
    filterPaths.add("/walletsolidity/scannotebyivk");
    filterPaths.add("/walletsolidity/scannotebyovk");
    filterPaths.add("/walletsolidity/gettransactioninfobyblocknum");
    filterPaths.add("/walletsolidity/getmarketorderbyaccount");
    filterPaths.add("/walletsolidity/getmarketorderbyid");
    filterPaths.add("/walletsolidity/getmarketpricebypair");
    filterPaths.add("/walletsolidity/getmarketorderlistbypair");
    filterPaths.add("/walletsolidity/getmarketpairlist");
    filterPaths.add("/walletsolidity/scanshieldedtrc20notesbyivk");
    filterPaths.add("/walletsolidity/scanshieldedtrc20notesbyovk");
    filterPaths.add("/walletsolidity/isshieldedtrc20contractnotespent");
```

**File:** framework/src/main/java/org/tron/core/services/filter/LiteFnQueryHttpFilter.java (L110-123)
```java
  @Override
  public void doFilter(ServletRequest servletRequest, ServletResponse servletResponse,
                       FilterChain filterChain) throws IOException, ServletException {
    String contextPath = ((HttpServletRequest) servletRequest).getContextPath();
    String requestPath = contextPath + ((HttpServletRequest) servletRequest).getServletPath();
    if (chainBaseManager.isLiteNode()
            && !CommonParameter.getInstance().openHistoryQueryWhenLiteFN
            && filterPaths.contains(requestPath)) {
      servletResponse.setContentType("application/json; charset=utf-8");
      servletResponse.getWriter().write("this API is closed because this node is a lite fullnode");
    } else {
      filterChain.doFilter(servletRequest, servletResponse);
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/ChainBaseManager.java (L392-406)
```java
  @PostConstruct
  private void init() {
    this.lowestBlockNum = this.blockIndexStore.getLimitNumber(1, 1).stream()
            .map(BlockId::getNum).findFirst().orElse(0L);
    this.nodeType = getLowestBlockNum() > 1 ? NodeType.LITE : NodeType.FULL;
    this.latestSaveBlockTime = System.currentTimeMillis();
  }

  public void shutdown() {
    dbStatService.shutdown();
  }

  public boolean isLiteNode() {
    return getNodeType() == NodeType.LITE;
  }
```
