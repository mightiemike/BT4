### Title
JSON-RPC `eth_getBlockByNumber` bypasses `LiteFnQueryHttpFilter` and reaches the same expensive `TransactionHistoryStore` lookup path that is blocked on lite fullnodes via HTTP - ([File: FullNodeJsonRpcHttpService.java])

### Summary
`FullNodeJsonRpcHttpService.addFilter` only registers `HttpInterceptor` on the `/jsonrpc` endpoint and never registers `LiteFnQueryHttpFilter`, unlike `FullNodeHttpApiService.addFilter` which explicitly blocks expensive historical queries (including `/wallet/gettransactioninfobyblocknum`) on lite fullnodes. Because `BlockResult(Block, boolean, Wallet)` unconditionally calls `wallet.getTransactionInfoByBlockNum(blockCapsule.getNum())` regardless of the `fullTx` flag, any unprivileged JSON-RPC caller can invoke `eth_getBlockByNumber` on old blocks to trigger the same per-transaction `TransactionHistoryStore` lookups that the lite-node HTTP filter is designed to block.

### Finding Description
`FullNodeHttpApiService.addFilter` (framework/src/main/java/org/tron/core/services/http/FullNodeHttpApiService.java:520-543) registers `liteFnQueryHttpFilter` on `/*`, and `LiteFnQueryHttpFilter.doFilter` (framework/src/main/java/org/tron/core/services/filter/LiteFnQueryHttpFilter.java:110-123) rejects requests matching `filterPaths` — including `/wallet/gettransactioninfobyblocknum` and `/walletsolidity/gettransactioninfobyblocknum` — whenever `chainBaseManager.isLiteNode()` is true and `openHistoryQueryWhenLiteFN` is false.

In contrast, `FullNodeJsonRpcHttpService.addFilter` (framework/src/main/java/org/tron/core/services/jsonrpc/FullNodeJsonRpcHttpService.java:35-43) only wires up `HttpInterceptor` for the `/jsonrpc` servlet path; `LiteFnQueryHttpFilter` is never imported, injected, or registered anywhere in this class.

The JSON-RPC method `eth_getBlockByNumber` constructs `BlockResult(Block, boolean, Wallet)` (framework/src/main/java/org/tron/core/services/jsonrpc/types/BlockResult.java:91-155). Regardless of whether the `fullTx` boolean argument is `true` or `false`, line 124-125 unconditionally executes:
```java
List<TransactionInfo> transactionInfoList =
    wallet.getTransactionInfoByBlockNum(blockCapsule.getNum()).getTransactionInfoList();
```
This flows into `Wallet.getTransactionInfoByBlockNum` -> `Manager.getTransactionInfoByBlockNum`, which is the exact same `TransactionHistoryStore` per-transaction lookup path that the HTTP `/wallet/gettransactioninfobyblocknum` servlet performs and that `LiteFnQueryHttpFilter` is specifically designed to gate on lite fullnodes. On a lite fullnode, this historical data is either pruned/absent (making the lookup consistently miss and iterate/return empty after DB probing) or, if history query support is partially enabled, forces disk/IO-bound lookups per transaction in the requested block — the same operation the filter exists to protect.

Because there is no equivalent check in the JSON-RPC path, and JSON-RPC calls are free (no fee, no energy/bandwidth cost, no signature or account requirement — `eth_getBlockByNumber` is a read-only query), an anonymous, unprivileged client can repeatedly call this method against historical block numbers to drive the same workload the operator explicitly opted to block via HTTP, defeating the intended metering/gating invariant.

### Impact Explanation
This is a DoS-via-RPC-API class issue: an anonymous, zero-cost JSON-RPC call can force a lite fullnode to perform unbounded/expensive per-transaction historical store lookups that the node operator has explicitly disabled via `LiteFnQueryHttpFilter`/`openHistoryQueryWhenLiteFN=false`. Repeated concurrent calls against many historical block numbers can degrade CPU/IO and node responsiveness for legitimate RPC/HTTP clients, and it circumvents an existing access-control mitigation that the operator deployed to prevent exactly this workload on lite nodes.

### Likelihood Explanation
- No privileges required: any anonymous client that can reach the `/jsonrpc` HTTP endpoint (enabled whenever `Args.getInstance().isJsonRpcHttpFullNodeEnable()` is true, which is a common/default-adjacent configuration for full nodes exposing JSON-RPC) can issue the request.
- No fee/signature/energy cost: `eth_getBlockByNumber` is a read-only RPC call, not a broadcast transaction, so there are no bandwidth/energy economics limiting repetition.
- Fully repeatable: the attacker can loop over historical block numbers and issue many parallel requests, with no rate limiter specific to this method visible in `JsonRpcServlet`/`FullNodeJsonRpcHttpService`.
- Precondition: node must be configured as a lite fullnode (`chainBaseManager.isLiteNode()` true) with history-query blocking intended — this is the exact scenario the missing filter fails to protect, and is a supported/documented node operation mode, not a non-default or contrived configuration.

### Recommendation
Register `LiteFnQueryHttpFilter` (or an equivalent JSON-RPC-aware filter that maps JSON-RPC method names to the same restriction) in `FullNodeJsonRpcHttpService.addFilter`, and/or add an explicit lite-node guard inside `TronJsonRpcImpl`/`BlockResult` before invoking `wallet.getTransactionInfoByBlockNum` when `chainBaseManager.isLiteNode()` is true and `openHistoryQueryWhenLiteFN` is false, mirroring the check already applied to the HTTP `/wallet/gettransactioninfobyblocknum` endpoint. Consider skipping the `getTransactionInfoByBlockNum` call entirely for `eth_getBlockByNumber` when `fullTx` is `false`, since gas/energy fields could be omitted or computed without transaction receipts in that case.

### Proof of Concept
```
POST /jsonrpc HTTP/1.1
Host: <lite-fullnode-host>:<jsonRpcHttpFullNodePort>
Content-Type: application/json

{"jsonrpc":"2.0","id":1,"method":"eth_getBlockByNumber","params":["0x1",true]}
```
Repeat this request (or loop over many historical block numbers, e.g. `0x1` through `0x100000`) with no authentication and no on-chain transaction cost. Compare against:
```
POST /wallet/gettransactioninfobyblocknum HTTP/1.1
Content-Type: application/json

{"num": 1}
```
which is rejected on a lite fullnode with `openHistoryQueryWhenLiteFN=false` (response body: `this API is closed because this node is a lite fullnode`) per `LiteFnQueryHttpFilter.doFilter` (framework/src/main/java/org/tron/core/services/filter/LiteFnQueryHttpFilter.java:115-122), while the equivalent `eth_getBlockByNumber` call succeeds and triggers the identical `TransactionHistoryStore`/`getTransactionInfoByBlockNum` code path via `BlockResult` (framework/src/main/java/org/tron/core/services/jsonrpc/types/BlockResult.java:124-125), confirming the filter bypass. A JUnit-level PoC would instantiate `FullNodeJsonRpcHttpService` and assert that `getFilters()`/`ServletContextHandler` filter mappings do not contain `LiteFnQueryHttpFilter`, contrasting with `FullNodeHttpApiServiceTest` equivalents that do. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/FullNodeJsonRpcHttpService.java (L30-43)
```java
  @Override
  protected void addServlet(ServletContextHandler context) {
    context.addServlet(new ServletHolder(jsonRpcServlet), "/jsonrpc");
  }

  @Override
  protected void addFilter(ServletContextHandler context) {
    // filter
    ServletHandler handler = new ServletHandler();
    FilterHolder fh = handler
        .addFilterWithMapping(HttpInterceptor.class, "/*",
            EnumSet.of(DispatcherType.REQUEST));
    context.addFilter(fh, "/*", EnumSet.of(DispatcherType.REQUEST));
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/FullNodeHttpApiService.java (L520-543)
```java
  @Override
  protected void addFilter(ServletContextHandler context) {
    // filters the specified APIs
    // when node is lite fullnode and openHistoryQueryWhenLiteFN is false
    context.addFilter(new FilterHolder(liteFnQueryHttpFilter), "/*",
        EnumSet.allOf(DispatcherType.class));

    // http access filter, it should have higher priority than HttpInterceptor
    context.addFilter(new FilterHolder(httpApiAccessFilter), "/*",
        EnumSet.allOf(DispatcherType.class));
    // note: if the pathSpec of servlet is not started with wallet, it should be included here
    context.getServletHandler().getFilterMappings()[1]
        .setPathSpecs(new String[] {"/wallet/*",
            "/net/listnodes",
            "/monitor/getstatsinfo",
            "/monitor/getnodeinfo"});

    // metrics filter
    ServletHandler handler = new ServletHandler();
    FilterHolder fh = handler
        .addFilterWithMapping((Class<? extends Filter>) HttpInterceptor.class, "/*",
            EnumSet.of(DispatcherType.REQUEST));
    context.addFilter(fh, "/*", EnumSet.of(DispatcherType.REQUEST));
  }
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

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/types/BlockResult.java (L119-126)
```java
    long gasUsedInBlock = 0;
    long gasLimitInBlock = 0;

    List<Object> txes = new ArrayList<>();
    List<Transaction> transactionsList = block.getTransactionsList();
    List<TransactionInfo> transactionInfoList =
        wallet.getTransactionInfoByBlockNum(blockCapsule.getNum()).getTransactionInfoList();
    if (fullTx) {
```
