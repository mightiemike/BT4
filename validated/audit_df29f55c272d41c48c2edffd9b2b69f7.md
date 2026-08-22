### Title
JSON-RPC batch fan-out bypasses per-request rate limiting, allowing CPU/I/O amplification via a single HTTP request - ([File: framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java])

### Summary
`RateLimiterServlet.service` consumes exactly one rate-limit permit per HTTP POST regardless of how many JSON-RPC sub-requests that POST contains, and `JsonRpcServlet.handleBatch` will execute up to `jsonRpcMaxBatchSize` (default 100) independent `TronJsonRpc` calls — including EVM-executing calls like `eth_call`/`eth_estimateGas` — inside that single request. An unprivileged client can therefore obtain up to a 100x CPU/I-O amplification per consumed rate-limit token compared to sending the same calls individually.

### Finding Description
`RateLimiterServlet.service()` acquires a permit and calls `super.service(req, resp)` exactly once per HTTP request [1](#0-0) . For `JsonRpcServlet`, that single `service()`/`doPost()` invocation can dispatch an entire JSON-RPC batch: `handleBatch` iterates over every element of the JSON array and calls `rpcServer.handleRequest(...)` once per sub-request, up to `jsonRpcMaxBatchSize` (default 100) items, each independently routed to any `TronJsonRpc` method [2](#0-1) [3](#0-2) .

The batch-size and response-size guards (`jsonRpcMaxBatchSize`, `jsonRpcMaxResponseSize`, default 100 and 25MB) only bound the number of sub-requests and the serialized output size — not the compute cost of each sub-request [4](#0-3) . Several `TronJsonRpc` methods reachable from a batch trigger full EVM execution or store lookups per call, e.g. `getCall`/`call` → `callTriggerConstantContract` → `wallet.triggerConstantContract` [5](#0-4) , and `estimateGas` → `estimateEnergy`/`callTriggerConstantContract` [6](#0-5) .

The rate limiter is configured per servlet (`JsonRpcServlet`), and by default (no entry in `rate.limiter.http`) falls back to `DefaultBaseQqsAdapter` with the default QPS strategy — a single QPS budget applied to the whole POST, independent of the batch's internal fan-out [7](#0-6) . Consequently, N sub-requests inside one POST consume the same "1 request" of rate-limit budget that a single non-batched call would, letting an attacker multiply the accepted compute-cost per rate-limited unit by up to `jsonRpcMaxBatchSize`.

None of the existing checks address this: `jsonRpcMaxBatchSize`/`jsonRpcMaxResponseSize` are count/byte limits, not cost limits; the rate limiter operates at the HTTP-request granularity by design; and constant/`eth_call`-style requests are not on-chain transactions, so they are not gated by transaction fees, energy purchase, or bandwidth accounting.

### Impact Explanation
This is a DoS-via-RPC-API amplification: a client within its allowed HTTP QPS can trigger up to `jsonRpcMaxBatchSize`× the CPU/I-O work per accepted request by packing expensive `eth_call`/`eth_estimateGas`/storage-lookup sub-requests into JSON-RPC batches, defeating the intent of the per-servlet rate limiter to bound compute cost over time. This can degrade node responsiveness/availability for other RPC clients under sustained load.

### Likelihood Explanation
Preconditions are just the default configuration (`jsonRpcMaxBatchSize = 100`, JSON-RPC HTTP enabled, no custom per-servlet rate-limit override — which is the shipped default in `reference.conf`/`config.conf`) [8](#0-7) . No signed transaction, fee payment, or privileged role is required — an anonymous HTTP client can send the batch. The attack is trivially repeatable and requires no special access; it is a pure protocol-level DoS design gap rather than a data-corruption or fund-theft bug.

### Recommendation
Account for batch fan-out in the rate limiter: either (a) charge the rate limiter proportionally to the number of sub-requests in a batch (e.g., acquire N permits for an N-item batch) instead of one permit per HTTP POST, or (b) apply a separate, lower-cost-weighted concurrency/QPS budget specifically to compute-heavy JSON-RPC methods (`eth_call`, `eth_estimateGas`, `eth_getStorageAt`, etc.) evaluated per sub-request inside `handleBatch`, or (c) impose a wall-clock/CPU budget for total batch processing time in `JsonRpcServlet.handleBatch` and abort/overflow remaining sub-requests once exceeded (similar to the existing response-size overflow handling).

### Proof of Concept
Raw RPC sequence (assuming default config, `jsonRpcMaxBatchSize=100`, `httpFullNodeEnable=true`):
```
POST /jsonrpc HTTP/1.1
Content-Type: application/json

[
  {"jsonrpc":"2.0","id":1,"method":"eth_call","params":[{"from":"0x0000000000000000000000000000000000000000","to":"<some deployed contract>","data":"0x<expensive view fn selector>"},"latest"]},
  {"jsonrpc":"2.0","id":2,"method":"eth_call","params":[{"from":"0x0000000000000000000000000000000000000000","to":"<some deployed contract>","data":"0x<expensive view fn selector>"},"latest"]},
  ... (repeat up to 100 entries)
]
```
Expected observation: this single POST consumes one rate-limiter permit in `RateLimiterServlet.service` (verifiable by instrumenting/mocking `IRateLimiter.acquirePermit` calls as done in `RateLimiterServletTest`), while `JsonRpcServlet.handleBatch` invokes `rpcServer.handleRequest` up to 100 times, each executing full VM logic via `callTriggerConstantContract`. Compare wall-clock/CPU time of this one POST versus 100 individually rate-limited single-call POSTs sent at the QPS limit — the batched path completes ~100x more EVM work per rate-limiter token, demonstrating the bypass of intended per-time-window cost enforcement (`JsonRpcServletTest.batchWithinLimit_proceedsToRpcServer` shows the mechanics of dispatching N sub-calls per single `doPost`) [9](#0-8) .

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java (L59-80)
```java
  @PostConstruct
  private void addRateContainer() {
    final String name = getClass().getSimpleName();
    RateLimiterInitialization.HttpRateLimiterItem item = Args.getInstance()
        .getRateLimiterInitialization().getHttpMap().get(name);

    String cName;
    String params;
    if (item == null) {
      cName = DEFAULT_ADAPTER_NAME;
      params = QpsStrategy.DEFAULT_QPS_PARAM;
    } else {
      cName = item.getStrategy();
      params = item.getParams();
    }

    try {
      container.add(KEY_PREFIX_HTTP, name, buildAdapter(cName, params, name));
    } catch (Exception e) {
      throw rateLimiterInitError(cName, params, name, e);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java (L103-132)
```java
  @Override
  protected void service(HttpServletRequest req, HttpServletResponse resp)
      throws ServletException, IOException {

    RuntimeData runtimeData = new RuntimeData(req);
    IRateLimiter rateLimiter = container.get(KEY_PREFIX_HTTP, getClass().getSimpleName());

    // Check per-endpoint first to avoid consuming global IP/QPS quota for requests
    // that would be rejected by the per-endpoint limiter anyway. acquirePermit()
    // chooses blocking or non-blocking semantics based on rate.limiter.apiNonBlocking.
    boolean perEndpointAcquired = rateLimiter == null || rateLimiter.acquirePermit(runtimeData);
    boolean acquireResource = perEndpointAcquired && GlobalRateLimiter.acquirePermit(runtimeData);

    String contextPath = req.getContextPath();
    String url = Strings.isNullOrEmpty(req.getServletPath())
        ? MetricLabels.UNDEFINED : contextPath + req.getServletPath();
    // int64_as_string is honored only on GET requests (URL query). POST is intentionally
    // unsupported because reading the body here would consume request.getReader() and
    // break downstream servlets that read it themselves.
    if ("GET".equalsIgnoreCase(req.getMethod())) {
      JsonFormat.setInt64AsString(Util.getInt64AsString(req));
    }
    try {
      resp.setContentType("application/json; charset=utf-8");

      if (acquireResource) {
        Histogram.Timer requestTimer = Metrics.histogramStartTimer(
            MetricKeys.Histogram.HTTP_SERVICE_LATENCY, url);
        super.service(req, resp);
        Metrics.histogramObserve(requestTimer);
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java (L131-147)
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
    } else {
      handleSingle(req, resp, rootNode, body, maxResponseSize);
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java (L181-224)
```java
    for (int i = 0; i < rootNode.size(); i++) {
      JsonNode subRequest = rootNode.get(i);

      if (overflow) {
        if (!subRequest.isObject()) {
          batchResult.add(buildErrorNode(JsonRpcError.INVALID_REQUEST, "Invalid Request", null));
        } else if (subRequest.has("id")) {
          // Notifications (no "id") do not get a response even on overflow.
          batchResult.add(buildErrorNode(JsonRpcError.RESPONSE_TOO_LARGE,
              "Response exceeds the limit of " + maxResponseSize + " bytes",
              subRequest.get("id")));
        }
        continue;
      }

      if (!subRequest.isObject()) {
        ObjectNode errNode = buildErrorNode(JsonRpcError.INVALID_REQUEST, "Invalid Request", null);
        byte[] errBytes = MAPPER.writeValueAsBytes(errNode);
        int addition = errBytes.length + (!batchResult.isEmpty() ? 1 : 0);
        if (maxResponseSize > 0 && accumulatedSize + addition > maxResponseSize) {
          overflow = true;
        } else {
          accumulatedSize += addition;
        }
        batchResult.add(errNode);
        continue;
      }

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

**File:** common/src/main/java/org/tron/common/parameter/CommonParameter.java (L480-484)
```java
  @Setter
  public int jsonRpcMaxBatchSize = 100;
  @Getter
  @Setter
  public int jsonRpcMaxResponseSize = 25 * 1024 * 1024;
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L466-489)
```java
  private void callTriggerConstantContract(byte[] ownerAddressByte, byte[] contractAddressByte,
      long value, byte[] data, TransactionExtention.Builder trxExtBuilder,
      Return.Builder retBuilder)
      throws ContractValidateException, ContractExeException, HeaderNotFound, VMIllegalException {

    TriggerSmartContract triggerContract = triggerCallContract(
        ownerAddressByte,
        contractAddressByte,
        value,
        data,
        0,
        null
    );

    TransactionCapsule trxCap = wallet.createTransactionCapsule(triggerContract,
        ContractType.TriggerSmartContract);
    Transaction trx =
        wallet.triggerConstantContract(triggerContract, trxCap, trxExtBuilder, retBuilder);

    trxExtBuilder.setTransaction(trx);
    trxExtBuilder.setTxid(trxCap.getTransactionId().getByteString());
    trxExtBuilder.setResult(retBuilder);
    retBuilder.setResult(true).setCode(response_code.SUCCESS);
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

**File:** framework/src/main/resources/config.conf (L154-166)
```text
  jsonrpc {
    httpFullNodeEnable = false
    httpFullNodePort = 8545

    maxBlockRange = 5000
    maxAddressSize = 1000
    maxSubTopics = 1000
    maxBlockFilterNum = 50000
    maxBatchSize = 100
    maxResponseSize = 26214400
    maxLogFilterNum = 20000
    maxMessageSize = 4194304
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
