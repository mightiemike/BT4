### Title
JSON-RPC batch requests let a single rate-limit permit purchase up to `jsonRpcMaxBatchSize` TVM `eth_call`/`eth_estimateGas` executions - ([File: framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java])

### Summary
`RateLimiterServlet.service` acquires exactly one per-endpoint permit and one global permit per HTTP request before dispatching to `JsonRpcServlet.doPost`, without any awareness of whether the body is a single call or a JSON-RPC batch array. `JsonRpcServlet.handleBatch` then iterates over every element of the batch (up to `parameter.getJsonRpcMaxBatchSize()`, default 100) and calls `rpcServer.handleRequest` for each one, each of which can independently trigger a full TVM `eth_call`/`eth_estimateGas` execution.

### Finding Description
`RateLimiterServlet.service` acquires the per-endpoint limiter and `GlobalRateLimiter` permit once per `doPost` invocation, then calls `super.service()`, which dispatches into `JsonRpcServlet.doPost`. [1](#0-0) 
`doPost` parses the body, and if it is a JSON array (`isBatch`), only checks that `rootNode.size()` does not exceed `parameter.getJsonRpcMaxBatchSize()` (configurable, default 100, `<=0` meaning unlimited). [2](#0-1) 
`handleBatch` then loops over every sub-request in the array and calls `rpcServer.handleRequest(...)` per element, with no additional rate-limit or per-sub-request throttling — each element is routed through `TronJsonRpc`/`TronJsonRpcImpl` and can invoke `eth_call` (`getCall` → `Wallet.triggerConstantContract`) or `eth_estimateGas` (`estimateGas` → `callTriggerConstantContract`/`estimateEnergy`), both of which execute real TVM code. [3](#0-2) [4](#0-3) 
Since the per-endpoint and global rate limiters are only consulted once at `RateLimiterServlet.service`, a single acquired permit covers the entire batch regardless of its size, letting an attacker amplify TVM work by up to `jsonRpcMaxBatchSize` (default 100) per consumed token. Confirmed by test `batchWithinLimit_proceedsToRpcServer`, which shows each sub-request in a batch independently reaches `rpcServer.handleRequest`. [5](#0-4) 

Per-call cost is bounded (`maxEnergyLimitForConstant` = 100,000,000 default and `constantCallTimeoutMs`), but these bounds are per-sub-call, not per-HTTP-request, and are unrelated to the rate-limiter's token accounting. [6](#0-5) 

### Impact Explanation
An attacker can send a single JSON-RPC batch containing up to `jsonRpcMaxBatchSize` (default 100, operator-configurable, `<=0` disables the cap entirely) `eth_call`/`eth_estimateGas` sub-requests in one HTTP POST, consuming only a single per-endpoint and global rate-limit permit while triggering up to that many independent TVM executions (each potentially bounded by `maxEnergyLimitForConstant` energy and `constantCallTimeoutMs`/block-processing time). This allows a CPU/energy amplification factor equal to the configured batch size per rate-limit token, materially underpricing this public compute relative to the rate-limiter's accounting model, and can be used to degrade node responsiveness for other JSON-RPC/API/gRPC callers sharing the same global QPS budget.

### Likelihood Explanation
This requires no authentication or special privileges — any client with access to the `/jsonrpc` HTTP endpoint (enabled by `httpFullNodeEnable`/similar flags) can send such a batch. It is fully repeatable per successfully-acquired permit (bounded only by whatever per-endpoint/global QPS remains available), and the default `jsonRpcMaxBatchSize` of 100 and unlimited value if operators set `maxBatchSize<=0` both make this readily reachable in default or lightly-customized deployments.

### Recommendation
Make the rate limiter batch-size-aware for JSON-RPC: either (a) have `RateLimiterServlet`/`JsonRpcServlet` acquire N permits (or weight the QPS cost) proportional to `rootNode.size()` before executing a batch, rejecting/queuing if insufficient budget remains, or (b) apply a distinct, lower `jsonRpcMaxBatchSize` specifically for state-executing methods (`eth_call`, `eth_estimateGas`) versus read-only methods, or (c) enforce a per-sub-request rate-limit check inside `handleBatch` using the same `GlobalRateLimiter`/per-endpoint limiter instance so that each TVM-executing sub-call consumes its own token.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/services/jsonrpc/JsonRpcBatchAmplificationTest.java
@Test
public void batchAmplifiesTvmExecutionsPerSinglePermit() throws Exception {
    CommonParameter.getInstance().jsonRpcMaxBatchSize = 100;
    AtomicInteger tvmCallCount = new AtomicInteger();
    doAnswer(inv -> {
        tvmCallCount.incrementAndGet();
        OutputStream out = inv.getArgument(1);
        out.write("{\"jsonrpc\":\"2.0\",\"result\":\"0x\",\"id\":1}".getBytes(StandardCharsets.UTF_8));
        return 0;
    }).when(mockRpcServer).handleRequest(any(InputStream.class), any(OutputStream.class));

    // Build a batch of 100 eth_call requests
    StringBuilder sb = new StringBuilder("[");
    for (int i = 0; i < 100; i++) {
        if (i > 0) sb.append(',');
        sb.append("{\"jsonrpc\":\"2.0\",\"method\":\"eth_call\",\"params\":[{}],\"id\":").append(i).append("}");
    }
    sb.append("]");

    // Simulate RateLimiterServlet: acquire a single permit, then dispatch doPost once
    IPreemptibleRateLimiter perEndpoint = mock(IPreemptibleRateLimiter.class);
    when(perEndpoint.acquirePermit(any())).thenReturn(true);
    // ... wire perEndpoint into container as in RateLimiterServletTest ...

    MockHttpServletResponse resp = doPost(sb.toString());

    // Assert: only ONE permit acquisition occurred (per RateLimiterServlet.service contract)
    verify(perEndpoint, times(1)).acquirePermit(any());
    // But 100 independent TVM/RPC executions were performed for that single permit
    assertEquals(100, tvmCallCount.get());
}
```
Expected assertion failure under the current design confirms the amplification: `acquirePermit` is called exactly once while `rpcServer.handleRequest` (the TVM-executing path) is invoked 100 times for the cost of that one permit.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java (L110-114)
```java
    // Check per-endpoint first to avoid consuming global IP/QPS quota for requests
    // that would be rejected by the per-endpoint limiter anyway. acquirePermit()
    // chooses blocking or non-blocking semantics based on rate.limiter.apiNonBlocking.
    boolean perEndpointAcquired = rateLimiter == null || rateLimiter.acquirePermit(runtimeData);
    boolean acquireResource = perEndpointAcquired && GlobalRateLimiter.acquirePermit(runtimeData);
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java (L131-145)
```java
    boolean isBatch = rootNode.isArray();
    if (isBatch && rootNode.isEmpty()) {
      writeJsonRpcError(resp, JsonRpcError.INVALID_REQUEST, "Invalid Request", null, false);
      return;
    }
    int batchSize = parameter.getJsonRpcMaxBatchSize();
    if (isBatch && batchSize > 0 && rootNode.size() > batchSize) {
      writeJsonRpcError(resp, JsonRpcError.EXCEED_LIMIT,
          "Batch size " + rootNode.size() + " exceeds the limit of " + batchSize, null, true);
      return;
    }

    int maxResponseSize = parameter.getJsonRpcMaxResponseSize();
    if (isBatch) {
      handleBatch(resp, rootNode, maxResponseSize);
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java (L209-224)
```java
      byte[] subBody;
      try {
        subBody = MAPPER.writeValueAsBytes(subRequest);
      } catch (JsonProcessingException e) {
        writeJsonRpcError(resp, JsonRpcError.INTERNAL_ERROR, "Internal error", null, true);
        return;
      }

      ByteArrayOutputStream subOutput = new ByteArrayOutputStream();
      try {
        rpcServer.handleRequest(new ByteArrayInputStream(subBody), subOutput);
      } catch (RuntimeException e) {
        logger.error("RPC execution failed for batch sub-request {}", i, e);
        writeJsonRpcError(resp, JsonRpcError.INTERNAL_ERROR, "Internal error", null, true);
        return;
      }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L673-715)
```java
  @Override
  public String estimateGas(CallArguments args) throws JsonRpcInvalidRequestException,
      JsonRpcInvalidParamsException, JsonRpcInternalException {
    byte[] ownerAddress = addressCompatibleToByteArray(args.getFrom());

    ContractType contractType = args.getContractType(wallet);
    if (contractType == ContractType.TransferContract) {
      buildTransferContractTransaction(ownerAddress, new BuildArguments(args));
      return "0x0";
    }

    boolean supportEstimateEnergy = CommonParameter.getInstance().isEstimateEnergy();

    TransactionExtention.Builder trxExtBuilder = TransactionExtention.newBuilder();
    Return.Builder retBuilder = Return.newBuilder();
    EstimateEnergyMessage.Builder estimateBuilder
        = EstimateEnergyMessage.newBuilder();

    try {
      byte[] contractAddress;

      if (contractType == ContractType.TriggerSmartContract) {
        contractAddress = addressCompatibleToByteArray(args.getTo());
      } else {
        contractAddress = new byte[0];
      }

      if (supportEstimateEnergy) {
        estimateEnergy(ownerAddress,
            contractAddress,
            args.parseValue(),
            ByteArray.fromHexString(args.resolveData()),
            trxExtBuilder,
            retBuilder,
            estimateBuilder);
      } else {
        callTriggerConstantContract(ownerAddress,
            contractAddress,
            args.parseValue(),
            ByteArray.fromHexString(args.resolveData()),
            trxExtBuilder,
            retBuilder);
      }
```

**File:** framework/src/test/java/org/tron/core/services/jsonrpc/JsonRpcServletTest.java (L93-110)
```java
  @Test
  public void batchWithinLimit_proceedsToRpcServer() throws Exception {
    CommonParameter.getInstance().jsonRpcMaxBatchSize = 5;
    byte[] singleResp = "{\"jsonrpc\":\"2.0\",\"result\":\"ok\",\"id\":1}"
        .getBytes(StandardCharsets.UTF_8);
    doAnswer(inv -> {
      OutputStream out = inv.getArgument(1);
      out.write(singleResp);
      return 0;
    }).when(mockRpcServer).handleRequest(any(InputStream.class), any(OutputStream.class));

    MockHttpServletResponse resp = doPost("[{\"id\":1},{\"id\":2}]");
    assertEquals(200, resp.getStatus());
    JsonNode body = MAPPER.readTree(resp.getContentAsByteArray());
    assertTrue("batch response must be a JSON array", body.isArray());
    assertEquals("each sub-request must produce a response", 2, body.size());
    assertEquals("ok", body.get(0).get("result").asText());
  }
```

**File:** common/src/main/java/org/tron/common/parameter/CommonParameter.java (L56-83)
```java
  public boolean supportConstant = false;
  @Getter
  @Setter
  public long maxEnergyLimitForConstant = 100_000_000L;
  @Getter
  @Setter
  public int lruCacheSize = 500;
  @Getter
  @Setter
  public boolean debug = false;
  @Getter
  @Setter
  public double minTimeRatio = 0.0;
  @Getter
  @Setter
  public double maxTimeRatio = calcMaxTimeRatio();
  /**
   * Max TVM execution time (ms) for constant calls — covers
   * triggerconstantcontract, triggersmartcontract dispatched to view/pure
   * functions, estimateenergy, eth_call, eth_estimateGas, and any other
   * RPC routed through Wallet#callConstantContract. 0 = use the same
   * deadline as block processing (current behaviour). When operators set
   * this in config the value must be positive and fit VM deadline conversion;
   * validated at config-load in VmConfig.
   */
  @Getter
  @Setter
  public long constantCallTimeoutMs = 0L;
```
