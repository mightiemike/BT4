### Title
Unbounded VM-execution amplification via JSON-RPC `eth_estimateGas`, multipliable via JSON-RPC batching - ([File: framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java])

### Summary
`eth_estimateGas` on the JSON-RPC surface routes to `Wallet.estimateEnergy`, which performs a binary-search loop that re-executes the target contract's bytecode via `cleanContextAndTriggerConstantContract` up to `log2(maxFeeLimit/step)` times per single RPC call, with retries on VM timeout. Because JSON-RPC batching (`jsonRpcMaxBatchSize`, default 100) only bounds the number of sub-requests and response byte size, not aggregate VM execution time, an unauthenticated client can pack many worst-case `eth_estimateGas` calls into one HTTP POST to multiply this per-call CPU cost within a single connection/request.

### Finding Description
`TronJsonRpcImpl.estimateGas` (`framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java:673-752`) calls the private `estimateEnergy(...)` helper (lines 491-514), which in turn calls `Wallet.estimateEnergy` (`framework/src/main/java/org/tron/core/Wallet.java:2986-3087`). That method runs a binary search over the fee-limit range `[low, high]`, calling `cleanContextAndTriggerConstantContract` repeatedly — an initial call, a doubling-search call, and then `O(log2(high/TRX_PRECISION))` bisection calls — each of which re-executes the entire target contract call in the TVM. Each execution is only bounded by the VM's own per-execution timeout (`Program.OutOfTimeException`), and on timeout the outer loop retries up to `estimateEnergyMaxRetry` more times (`framework/src/main/java/org/tron/core/Wallet.java:2999-3016`, `3030-3047`, `3054-3064`).

This exact multiplication effect already exists on the gRPC/HTTP `EstimateEnergy` path (`framework/src/main/java/org/tron/core/services/RpcApiService.java:865-895`, `framework/src/main/java/org/tron/core/services/http/EstimateEnergyServlet.java`), and the JSON-RPC `eth_estimateGas` method reaches the identical `Wallet.estimateEnergy` code path, so it inherits the same per-call VM-execution amplification.

On top of that, `JsonRpcServlet.doPost`/`handleBatch` (`framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java:104-149, 174-266`) allows a single HTTP request to contain up to `jsonRpcMaxBatchSize` (default 100, config in `common/src/main/resources/reference.conf:436-437` and `NodeConfig.JsonRpcConfig`) sub-requests, each independently dispatched to `rpcServer.handleRequest`, i.e., each independently triggering its own full `estimateEnergy` binary search. `maxMessageSize`/`maxResponseSize` bound request/response byte sizes only, and `maxBatchSize` bounds sub-request count only — neither caps the aggregate TVM execution time consumed processing the batch. There is no visible batch-level or aggregate CPU/time budget check in `JsonRpcServlet` or `Wallet.estimateEnergy`.

### Impact Explanation
This maps to the "DoS via RPC-API" impact class: a single unauthenticated HTTP POST containing a JSON-RPC batch of `eth_estimateGas` calls against attacker-deployed, execution-expensive contracts can force the node to perform up to 100× the per-call binary-search VM re-execution amplification, consuming disproportionate CPU on the node handling the request. This can degrade or stall the node's ability to serve other RPC/API consumers or process other work, without the attacker paying any on-chain fee (no transaction broadcast, no signature, no bandwidth/energy consumption is charged since these calls are read-only estimation paths).

### Likelihood Explanation
Preconditions are default/likely-production settings: `jsonrpc.httpFullNodeEnable=true` (JSON-RPC enabled) and default `maxBatchSize=100`. The attacker only needs to be an anonymous RPC client — no privileged role, no broadcast transaction, no fee payment. Deploying an expensive contract requires only a normal funded account performing a one-time deployment; the amplification is then exploitable repeatedly and at no further cost from any client with network access to the JSON-RPC endpoint. This is highly repeatable (bounded only by attacker's ability to send HTTP requests) and requires no special node configuration beyond enabling JSON-RPC, which is a common deployment choice.

### Recommendation
- Add an aggregate CPU/time budget for JSON-RPC batch requests (and gRPC/HTTP `EstimateEnergy`), independent of `maxBatchSize`/`maxMessageSize`, e.g., a wall-clock or CPU-time cap per HTTP request that aborts remaining batch items once exceeded.
- Cap or reduce the number of VM re-executions in `Wallet.estimateEnergy`'s binary search (e.g., tighter iteration bound or smaller `estimateEnergyMaxRetry`), and/or apply a stricter, non-configurable per-call timeout independent of `Program.OutOfTimeException` retries.
- Apply a dedicated rate limiter (QPS/concurrency) specifically for `eth_estimateGas`/`EstimateEnergy`-class methods, separate from generic JSON-RPC batch limits, since these are the most CPU-expensive read-only calls.
- Consider excluding or specially throttling gas-estimation methods when they appear inside JSON-RPC batches, since batching legitimate use cases (balance/log queries) rarely needs to include expensive VM-executing methods at high multiplicity.

### Proof of Concept
Conceptual request-level PoC (requires a running node with `jsonrpc.httpFullNodeEnable=true` and `estimateEnergy=true`, plus an attacker-deployed contract `C` at address `ADDR` with a function `worstCase()` designed to consume near-maximal energy/time per execution):

```
POST /jsonrpc HTTP/1.1
Content-Type: application/json

[
  {"jsonrpc":"2.0","id":1,"method":"eth_estimateGas","params":[{"from":"0x...","to":"ADDR","data":"0x<worstCase selector>"}]},
  {"jsonrpc":"2.0","id":2,"method":"eth_estimateGas","params":[{"from":"0x...","to":"ADDR","data":"0x<worstCase selector>"}]},
  ... (up to 100 entries, the default jsonRpcMaxBatchSize)
]
```

Expected observation: the node's CPU time to process this single HTTP request is approximately `100 × (per-call binary-search VM re-execution cost)`, where the per-call cost itself is a multiple (via `Wallet.estimateEnergy`'s `log2` bisection loop, `framework/src/main/java/org/tron/core/Wallet.java:2986-3087`) of a single worst-case contract execution — demonstrating that `maxBatchSize`/`maxMessageSize` bound request shape but not aggregate VM execution cost, as described in the question's proof idea. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L491-514)
```java
  private void estimateEnergy(byte[] ownerAddressByte, byte[] contractAddressByte,
      long value, byte[] data, TransactionExtention.Builder trxExtBuilder,
      Return.Builder retBuilder, EstimateEnergyMessage.Builder estimateBuilder)
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
        wallet.estimateEnergy(triggerContract, trxCap, trxExtBuilder, retBuilder, estimateBuilder);
    trxExtBuilder.setTransaction(trx);
    trxExtBuilder.setTxid(trxCap.getTransactionId().getByteString());
    trxExtBuilder.setResult(retBuilder);
    retBuilder.setResult(true).setCode(response_code.SUCCESS);
    estimateBuilder.setResult(retBuilder);
  }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L673-752)
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

    } catch (ContractValidateException e) {
      String errString = "invalid contract";
      if (e.getMessage() != null) {
        errString = e.getMessage();
      }

      throw new JsonRpcInvalidRequestException(errString);
    } catch (Exception e) {
      String errString = JSON_ERROR;
      if (e.getMessage() != null) {
        errString = e.getMessage().replaceAll("[\"]", "'");
      }

      throw new JsonRpcInternalException(errString);
    }

    if (trxExtBuilder.getTransaction().getRet(0).getRet().equals(code.FAILED)) {
      byte[] data = trxExtBuilder.getConstantResult(0).toByteArray();
      String errMsg = retBuilder.getMessage().toStringUtf8() + tryDecodeRevertReason(data);

      if (data.length > 0) {
        throw new JsonRpcInternalException(errMsg, ByteArray.toJsonHex(data));
      } else {
        throw new JsonRpcInternalException(errMsg);
      }

    } else {

      if (supportEstimateEnergy) {
        return ByteArray.toJsonHex(estimateBuilder.getEnergyRequired());
      } else {
        return ByteArray.toJsonHex(trxExtBuilder.getEnergyUsed());
      }

    }
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2986-3087)
```java
  public Transaction estimateEnergy(TriggerSmartContract triggerSmartContract,
      TransactionCapsule txCap, TransactionExtention.Builder txExtBuilder,
      Return.Builder txRetBuilder, GrpcAPI.EstimateEnergyMessage.Builder estimateBuilder)
      throws ContractValidateException, ContractExeException, HeaderNotFound, VMIllegalException {

    if (!Args.getInstance().estimateEnergy) {
      throw new ContractValidateException("this node does not support estimate energy");
    }

    if (!Args.getInstance().supportConstant) {
      throw new ContractValidateException("this node does not support constant, "
          + "so estimate energy cannot work");
    }
    int retry = Args.getInstance().estimateEnergyMaxRetry;

    DynamicPropertiesStore dps = chainBaseManager.getDynamicPropertiesStore();
    long high = dps.getMaxFeeLimit();

    Transaction transaction;

    while (true) {
      try {
        transaction = cleanContextAndTriggerConstantContract(
            triggerSmartContract, txCap, txExtBuilder, txRetBuilder, high);
        break;
      } catch (Program.OutOfTimeException e) {
        retry--;
        if (retry < 0) {
          throw e;
        }
      }
    }

    // If failed, return directly.
    if (transaction.getRet(0).getRet().equals(code.FAILED)) {
      txRetBuilder.setCode(response_code.CONTRACT_EXE_ERROR);
      estimateBuilder.setResult(txRetBuilder);
      return transaction;
    }

    long low = dps.getEnergyFee() * txExtBuilder.getEnergyUsed();

    long twoTimes = low * 2;
    if (twoTimes < high) {
      while (true) {
        try {
          transaction = cleanContextAndTriggerConstantContract(
              triggerSmartContract, txCap, txExtBuilder, txRetBuilder, twoTimes);

          if (transaction.getRet(0).getRet().equals(code.FAILED)) {
            low = twoTimes;
          } else {
            high = twoTimes;
          }

          break;
        } catch (Program.OutOfTimeException e) {
          retry--;
          if (retry < 0) {
            throw e;
          }
        }
      }
    }

    while (low + TRX_PRECISION < high) {
      long mid = (low + high) / 2;

      while (true) {
        try {
          transaction = cleanContextAndTriggerConstantContract(
              triggerSmartContract, txCap, txExtBuilder, txRetBuilder, mid);
          break;
        } catch (Program.OutOfTimeException e) {
          retry--;
          if (retry < 0) {
            throw e;
          }
        }
      }

      if (transaction.getRet(0).getRet().equals(code.FAILED)) {
        low = mid;
      } else {
        high = mid;
      }
    }

    // Retry the binary search result
    transaction = cleanContextAndTriggerConstantContract(
        triggerSmartContract, txCap, txExtBuilder, txRetBuilder, high);
    // Setting estimating result
    estimateBuilder.setResult(txRetBuilder);
    if (transaction.getRet(0).getRet().equals(code.SUCESS)) {
      txRetBuilder.setResult(true);
      txRetBuilder.setCode(response_code.SUCCESS);
      estimateBuilder.setEnergyRequired((long) ceil((double) high / dps.getEnergyFee(),
          dps.disableJavaLangMath()));
    }

    return transaction;
  }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java (L104-266)
```java
  @Override
  protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws IOException {
    CommonParameter parameter = CommonParameter.getInstance();

    // Transport IOException from readBody propagates as HTTP 500 (genuine IO failure).
    byte[] body = readBody(req.getInputStream());
    JsonNode rootNode;
    try {
      rootNode = MAPPER.readTree(body);
      if (rootNode == null || rootNode.isMissingNode()) {
        writeJsonRpcError(resp, JsonRpcError.PARSE_ERROR, "JSON parse error", null, false);
        return;
      }
    } catch (JsonProcessingException e) {
      if (e instanceof StreamConstraintsException) {
        writeJsonRpcError(resp, JsonRpcError.PARSE_ERROR, e.getMessage(), null, false);
      } else {
        writeJsonRpcError(resp, JsonRpcError.PARSE_ERROR, "JSON parse error", null, false);
      }
      return;
    }

    if (!rootNode.isObject() && !rootNode.isArray()) {
      writeJsonRpcError(resp, JsonRpcError.INVALID_REQUEST, "Invalid Request", null, false);
      return;
    }

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
    }
  }

  private void handleSingle(HttpServletRequest req, HttpServletResponse resp,
      JsonNode rootNode, byte[] body, int maxResponseSize) throws IOException {
    CachedBodyRequestWrapper cachedReq = new CachedBodyRequestWrapper(req, body);
    BufferedResponseWrapper bufferedResp = new BufferedResponseWrapper(
        resp, maxResponseSize);

    try {
      rpcServer.handle(cachedReq, bufferedResp);
    } catch (RuntimeException e) {
      logger.error("RPC execution failed", e);
      writeJsonRpcError(resp, JsonRpcError.INTERNAL_ERROR, "Internal error",
          rootNode.get("id"), false);
      return;
    }

    bufferedResp.commitToResponse();
    if (bufferedResp.isOverflow()) {
      writeJsonRpcError(resp, JsonRpcError.RESPONSE_TOO_LARGE,
          "Response exceeds the limit of " + maxResponseSize + " bytes",
          rootNode.get("id"), false);
    }
  }

  private void handleBatch(HttpServletResponse resp, JsonNode rootNode, int maxResponseSize)
      throws IOException {

    ArrayNode batchResult = MAPPER.createArrayNode();
    int accumulatedSize = 2; // "[]"
    boolean overflow = false;

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

      byte[] responseBytes = subOutput.toByteArray();
      if (responseBytes.length == 0) {
        continue; // notification — no response
      }

      // comma(,) separator between array elements
      int addition = responseBytes.length + (!batchResult.isEmpty() ? 1 : 0);
      if (maxResponseSize > 0 && accumulatedSize + addition > maxResponseSize) {
        overflow = true;
        batchResult.add(buildErrorNode(JsonRpcError.RESPONSE_TOO_LARGE,
            "Response exceeds the limit of " + maxResponseSize + " bytes",
            subRequest.get("id")));
        continue;
      }
      accumulatedSize += addition;

      JsonNode responseNode;
      try {
        responseNode = MAPPER.readTree(responseBytes);
      } catch (IOException e) {
        writeJsonRpcError(resp, JsonRpcError.INTERNAL_ERROR, "Internal error", null, true);
        return;
      }
      batchResult.add(responseNode);
    }

    // JSON-RPC 2.0 §6: MUST NOT return an empty Array when there are no response objects.
    if (batchResult.isEmpty()) {
      resp.setContentType("application/json-rpc");
      resp.setStatus(HttpServletResponse.SC_OK);
      resp.setContentLength(0);
      return;
    }

    byte[] finalBytes = MAPPER.writeValueAsBytes(batchResult);
    resp.setContentType("application/json-rpc");
    resp.setStatus(HttpServletResponse.SC_OK);
    resp.setContentLength(finalBytes.length);
    resp.getOutputStream().write(finalBytes);
    resp.getOutputStream().flush();
  }
```

**File:** common/src/main/resources/reference.conf (L417-444)
```text
  # JSON-RPC API settings.
  jsonrpc {
    # Note: Before release_4.8.1, if you turn on jsonrpc and run it for a while and then turn it off,
    # you will not be able to get the data from eth_getLogs for that period of time. Default: false
    httpFullNodeEnable = false
    httpFullNodePort = 8545     # FullNode JSON-RPC HTTP port.
    httpSolidityEnable = false  # Whether to enable Solidity JSON-RPC HTTP API.
    httpSolidityPort = 8555     # Solidity JSON-RPC HTTP port.
    httpPBFTEnable = false      # Whether to enable PBFT JSON-RPC HTTP API.
    httpPBFTPort = 8565         # PBFT JSON-RPC HTTP port.

    # The maximum blocks range to retrieve logs for eth_getLogs, default: 5000, <=0 means no limit
    maxBlockRange = 5000
    # Allowed max address count in filter request, default: 1000, <=0 means no limit
    maxAddressSize = 1000
    # The maximum number of allowed topics within a topic criteria, default: 1000, <=0 means no limit
    maxSubTopics = 1000
    # Allowed maximum number for blockFilter, default: 50000, <=0 means no limit
    maxBlockFilterNum = 50000
    # Allowed batch size, default: 100, <=0 means no limit
    maxBatchSize = 100
    # Allowed max response byte size, default: 26214400 (25 MB), <=0 means no limit
    maxResponseSize = 26214400
    # Allowed maximum number for newFilter, <=0 means no limit
    maxLogFilterNum = 20000
    # Maximum JSON-RPC request body size in bytes (default 4194304, ~4MB). Independent from rpc.maxMessageSize.
    maxMessageSize = 4194304
  }
```

**File:** common/src/main/java/org/tron/core/config/args/NodeConfig.java (L231-250)
```java
  @Getter
  @Setter
  public static class JsonRpcConfig {

    private boolean httpFullNodeEnable = false;
    private int httpFullNodePort = 8545;
    private boolean httpSolidityEnable = false;
    private int httpSolidityPort = 8555;
    private boolean httpPBFTEnable = false;
    private int httpPBFTPort = 8565;

    private int maxBlockRange = 5000;
    private int maxSubTopics = 1000;
    private int maxBlockFilterNum = 50000;
    private int maxBatchSize = 100;
    private int maxResponseSize = 25 * 1024 * 1024;
    private int maxAddressSize = 1000;
    private int maxLogFilterNum = 20000;
    private long maxMessageSize = 4194304;
  }
```

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L865-895)
```java
    @Override
    public void estimateEnergy(TriggerSmartContract request,
        StreamObserver<EstimateEnergyMessage> responseObserver) {
      TransactionExtention.Builder trxExtBuilder = TransactionExtention.newBuilder();
      Return.Builder retBuilder = Return.newBuilder();
      EstimateEnergyMessage.Builder estimateBuilder
          = EstimateEnergyMessage.newBuilder();

      try {
        TransactionCapsule trxCap = createTransactionCapsule(request,
            ContractType.TriggerSmartContract);
        wallet.estimateEnergy(request, trxCap, trxExtBuilder, retBuilder, estimateBuilder);
      } catch (ContractValidateException | VMIllegalException e) {
        retBuilder.setResult(false).setCode(response_code.CONTRACT_VALIDATE_ERROR)
            .setMessage(ByteString.copyFromUtf8(Wallet
                .CONTRACT_VALIDATE_ERROR + e.getMessage()));
        logger.warn(CONTRACT_VALIDATE_EXCEPTION, e.getMessage());
      } catch (RuntimeException e) {
        retBuilder.setResult(false).setCode(response_code.CONTRACT_EXE_ERROR)
            .setMessage(ByteString.copyFromUtf8(e.getClass() + " : " + e.getMessage()));
        logger.warn("When run estimate energy in VM, have Runtime Exception: " + e.getMessage());
      } catch (Exception e) {
        retBuilder.setResult(false).setCode(response_code.OTHER_ERROR)
            .setMessage(ByteString.copyFromUtf8(e.getClass() + " : " + e.getMessage()));
        logger.warn(UNKNOWN_EXCEPTION_CAUGHT + e.getMessage(), e);
      } finally {
        estimateBuilder.setResult(retBuilder);
        responseObserver.onNext(estimateBuilder.build());
        responseObserver.onCompleted();
      }
    }
```
