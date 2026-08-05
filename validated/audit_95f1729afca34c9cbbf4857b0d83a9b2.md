### Title
Unmetered CPU/IO amplification via `GetMerkleTreeVoucherInfo` with maxed `OutputPointInfo` under fixed per-endpoint QPS limiter - ([File: framework/src/main/java/org/tron/core/Wallet.java])

### Summary
`Wallet.getMerkleTreeVoucherInfo` allows an unauthenticated caller to request `blockNum` up to 1000 and up to 10 `OutputPoint`s per call, each of which triggers `createWitness()` (a full block scan + Pedersen-hash append chain), `updateLowWitness()`, and `updateWitnesses()` (each scanning up to 1000 blocks and appending every shielded note commitment for every witness). The HTTP/gRPC rate limiters (`RateLimiterServlet`/`QpsStrategy`/`GlobalRateLimiter`) charge a fixed per-request token regardless of these parameters, so a minimal and a maximal request cost the same rate-limit budget while consuming vastly different CPU/disk resources.

### Finding Description
The entrypoints `GetMerkleTreeVoucherInfoServlet.doPost` and the gRPC `getMerkleTreeVoucherInfo` handlers in `RpcApiService.java`/`RpcApiServiceOnSolidity.java` directly forward the client-supplied `OutputPointInfo` to `Wallet.getMerkleTreeVoucherInfo(request)` [1](#0-0) [2](#0-1) .

`validateInput` only bounds `blockNum` to `[0,1000]` and `outPointsCount` to `[1,10]`, it does not reject the combination on cost grounds [3](#0-2) . For each of up to 10 `OutputPoint`s, `createWitness()` scans the entire target block's transaction list and runs a Pedersen-hash `tree.append`/`witness.append` chain over every `ShieldedTransferContract` receive description in that block [4](#0-3) . `updateLowWitness()` then re-scans blocks between each output's block and the max block among the batch, again appending every commitment found [5](#0-4) . If `blockNum != 0`, `updateWitnesses()` additionally scans up to `synBlockNum` (≤1000) further blocks for all witnesses simultaneously [6](#0-5) . Finally, `MerklePath.encode()` is invoked once per witness in the response-building loop [7](#0-6) .

The only gating condition is `checkAllowShieldedTransactionApi()`, which is a node-level feature flag (shielded transactions enabled), not an authorization or cost control [8](#0-7) .

On the rate-limiting side, `RateLimiterServlet.service()` acquires a fixed per-endpoint permit via `IRateLimiter.acquirePermit()` and a `GlobalRateLimiter` permit, both are flat token-bucket/QPS constructs (`QpsStrategy` wraps Guava's `RateLimiter.create(qps)`) that do not inspect request payload size, `blockNum`, or `outPointsCount` [9](#0-8) [10](#0-9) . Thus a `blockNum=0, outPointsCount=1` request and a `blockNum=1000, outPointsCount=10` request consume identical rate-limit tokens while the latter does on the order of ~10x more full-block scans for `createWitness`/`updateLowWitness` (per output point) plus up to 10 concurrent witness updates across 1000 blocks in `updateWitnesses`, and re-runs Pedersen combine chains for every commitment encountered in all scanned blocks.

### Impact Explanation
This is a public, unauthenticated CPU/disk-IO amplification vector: an attacker can drive full-node/solidity-node resource consumption (block store reads, protobuf unpacking, cryptographic Pedersen hash chaining) far above what the per-endpoint rate limiter's flat token cost implies, at the maximum sustained QPS the limiter allows. Repeated maximal requests at the endpoint's allowed QPS can degrade node responsiveness for other legitimate API/gRPC consumers (denial-of-service via resource starvation) once shielded transactions are enabled on that node.

### Likelihood Explanation
Precondition: node must have shielded transaction API enabled (`checkAllowShieldedTransactionApi`), which is a network-level config, not attacker-controlled, but is expected to be enabled on TRON mainnet/testnet full nodes serving shielded API. Given that, the attack requires no authentication, no fee, and no special privilege — any HTTP or gRPC client can submit `OutputPointInfo` with `blockNum=1000` and 10 `out_points`, repeated at the endpoint's configured QPS indefinitely, since nothing in `validateInput` or the rate limiter differentiates cost tiers.

### Recommendation
Introduce cost-aware throttling for `getMerkleTreeVoucherInfo`: e.g., scale the rate-limiter cost (or apply a separate weighted limiter/semaphore) by `blockNum * outPointsCount`, cap the total blocks scanned per request more conservatively, or require proportionally higher token consumption for large `blockNum`/`outPointsCount` combinations so the per-request rate-limit cost tracks actual CPU/disk work.

### Proof of Concept
Java benchmark test (JMH-style or simple `System.nanoTime()`/thread CPU time measurement) added under `framework/src/test/java/org/tron/core/zksnark/`:
1. Seed a test chain with ≥1000 blocks each containing a `ShieldedTransferContract` with multiple `ReceiveDescription`s (reuse fixtures from `MerkleContainerTest.initMerkleTreeWitnessInfo`).
2. Call `wallet.getMerkleTreeVoucherInfo(minimalRequest)` where `minimalRequest` has `blockNum=0`, `outPointsCount=1`, measuring wall-clock and CPU time and disk-read count (via `chainBaseManager.getBlockByNum` invocation count instrumentation or `-Xss`/profiler hook).
3. Call `wallet.getMerkleTreeVoucherInfo(maximalRequest)` where `maximalRequest` has `blockNum=1000`, `outPointsCount=10` (10 distinct `OutputPoint`s spread across blocks).
4. Assert `maximalCost / minimalCost` (CPU time, block-read count) is orders of magnitude larger than 1, while the number of rate-limiter tokens consumed by both calls through `RateLimiterServlet`/`QpsStrategy` is identical (assert `tryAcquire()` cost model returns the same boolean/token debit regardless of request size), demonstrating the public-cost invariant violation.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetMerkleTreeVoucherInfoServlet.java (L24-38)
```java
  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      OutputPointInfo.Builder build = OutputPointInfo.newBuilder();
      JsonFormat.merge(params.getParams(), build);
      IncrementalMerkleVoucherInfo reply = wallet.getMerkleTreeVoucherInfo(build.build());
      if (reply != null) {
        response.getWriter().println(JsonFormat.printToString(reply, params.isVisible()));
      } else {
        response.getWriter().println("{}");
      }
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L661-673)
```java
    @Override
    public void getMerkleTreeVoucherInfo(OutputPointInfo request,
        StreamObserver<IncrementalMerkleVoucherInfo> responseObserver) {

      try {
        IncrementalMerkleVoucherInfo witnessInfo = wallet
            .getMerkleTreeVoucherInfo(request);
        responseObserver.onNext(witnessInfo);
      } catch (Exception ex) {
        responseObserver.onError(getRunTimeException(ex));
      }
      responseObserver.onCompleted();
    }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2008-2063)
```java
    boolean found = false;
    for (Transaction transaction : block.getInstance().getTransactionsList()) {

      Contract contract = transaction.getRawData().getContract(0);
      if (contract.getType() == ContractType.ShieldedTransferContract) {
        ShieldedTransferContract zkContract = contract.getParameter()
            .unpack(ShieldedTransferContract.class);

        if (new TransactionCapsule(transaction).getTransactionId().getByteString().equals(txId)) {
          found = true;

          if (outPoint.getIndex() >= zkContract.getReceiveDescriptionCount()) {
            throw new RuntimeException("outPoint.getIndex():" + outPoint.getIndex()
                + " >= zkContract.getReceiveDescriptionCount():" + zkContract
                .getReceiveDescriptionCount());
          }

          int index = 0;
          for (ReceiveDescription receiveDescription : zkContract.getReceiveDescriptionList()) {
            PedersenHashCapsule cmCapsule = new PedersenHashCapsule();
            cmCapsule.setContent(receiveDescription.getNoteCommitment());
            PedersenHash cm = cmCapsule.getInstance();

            if (index < outPoint.getIndex()) {
              tree.append(cm);
            } else if (outPoint.getIndex() == index) {
              tree.append(cm);
              witness = tree.getTreeCapsule().deepCopy()
                  .toMerkleTreeContainer().toVoucher();
            } else {
              if (witness != null) {
                witness.append(cm);
              } else {
                throw new ZksnarkException("witness is null!");
              }
            }

            index++;
          }

        } else {
          for (ReceiveDescription receiveDescription :
              zkContract.getReceiveDescriptionList()) {
            PedersenHashCapsule cmCapsule = new PedersenHashCapsule();
            cmCapsule.setContent(receiveDescription.getNoteCommitment());
            PedersenHash cm = cmCapsule.getInstance();
            if (witness != null) {
              witness.append(cm);
            } else {
              tree.append(cm);
            }

          }
        }
      }
    }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2073-2111)
```java
  private void updateWitnesses(List<IncrementalMerkleVoucherContainer> witnessList, long large,
      int synBlockNum) throws ItemNotFoundException, BadItemException,
      InvalidProtocolBufferException, ZksnarkException {
    long start = large;
    long end = large + synBlockNum - 1;

    long latestBlockHeaderNumber = chainBaseManager.getDynamicPropertiesStore()
        .getLatestBlockHeaderNumber();

    if (end > latestBlockHeaderNumber) {
      throw new RuntimeException(
          "synBlockNum is too large, cmBlockNum plus synBlockNum must be <= latestBlockNumber");
    }

    for (long n = start; n <= end; n++) {
      BlockCapsule block = chainBaseManager.getBlockByNum(n);
      for (Transaction transaction1 : block.getInstance().getTransactionsList()) {

        Contract contract1 = transaction1.getRawData().getContract(0);
        if (contract1.getType() == ContractType.ShieldedTransferContract) {

          ShieldedTransferContract zkContract = contract1.getParameter()
              .unpack(ShieldedTransferContract.class);

          for (ReceiveDescription receiveDescription :
              zkContract.getReceiveDescriptionList()) {

            PedersenHashCapsule cmCapsule = new PedersenHashCapsule();
            cmCapsule.setContent(receiveDescription.getNoteCommitment());
            PedersenHash cm = cmCapsule.getInstance();
            for (IncrementalMerkleVoucherContainer wit : witnessList) {
              wit.append(cm);
            }
          }

        }
      }
    }
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2113-2147)
```java
  private void updateLowWitness(IncrementalMerkleVoucherContainer witness, long blockNum1,
      long blockNum2) throws ItemNotFoundException, BadItemException,
      InvalidProtocolBufferException, ZksnarkException {
    long start;
    long end;
    if (blockNum1 < blockNum2) {
      start = blockNum1 + 1;
      end = blockNum2;
    } else {
      return;
    }

    for (long n = start; n <= end; n++) {
      BlockCapsule block = chainBaseManager.getBlockByNum(n);
      for (Transaction transaction1 : block.getInstance().getTransactionsList()) {

        Contract contract1 = transaction1.getRawData().getContract(0);
        if (contract1.getType() == ContractType.ShieldedTransferContract) {

          ShieldedTransferContract zkContract = contract1.getParameter()
              .unpack(ShieldedTransferContract.class);

          for (ReceiveDescription receiveDescription :
              zkContract.getReceiveDescriptionList()) {

            PedersenHashCapsule cmCapsule = new PedersenHashCapsule();
            cmCapsule.setContent(receiveDescription.getNoteCommitment());
            PedersenHash cm = cmCapsule.getInstance();
            witness.append(cm);
          }

        }
      }
    }
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2149-2170)
```java
  private void validateInput(OutputPointInfo request) throws BadItemException {
    if (request.getBlockNum() < 0 || request.getBlockNum() > 1000) {
      throw new BadItemException("request.BlockNum must be specified with range in [0, 1000]");
    }

    if (request.getOutPointsCount() < 1 || request.getOutPointsCount() > 10) {
      throw new BadItemException("request.OutPointsCount must be speccified with range in [1, 10]");
    }

    for (OutputPoint outputPoint : request.getOutPointsList()) {

      if (outputPoint.getHash() == null) {
        throw new BadItemException("outPoint.getHash() == null");
      }
      if (outputPoint.getIndex() >= Constant.ZC_OUTPUT_DESC_MAX_SIZE
          || outputPoint.getIndex() < 0) {
        throw new BadItemException(
            "outPoint.getIndex() > " + Constant.ZC_OUTPUT_DESC_MAX_SIZE
                + " || outPoint.getIndex() < 0");
      }
    }
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2172-2177)
```java
  public IncrementalMerkleVoucherInfo getMerkleTreeVoucherInfo(OutputPointInfo request)
      throws ItemNotFoundException, BadItemException,
      InvalidProtocolBufferException, ZksnarkException {
    checkAllowShieldedTransactionApi();

    validateInput(request);
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2211-2215)
```java
    for (IncrementalMerkleVoucherContainer w : witnessList) {
      w.getVoucherCapsule().resetRt();
      result.addVouchers(w.getVoucherCapsule().getInstance());
      result.addPaths(ByteString.copyFrom(w.path().encode()));
    }
```

**File:** framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java (L103-136)
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
      } else {
        resp.getWriter()
            .println(Util.printErrorMsg(new IllegalAccessException("lack of computing resources")));
      }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/strategy/QpsStrategy.java (L16-36)
```java
  public QpsStrategy(String paramString) {
    super(paramString);
    rateLimiter = RateLimiter.create((Double) mapParams.get(STRATEGY_PARAM_QPS).value);
  }

  // define the default strategy params
  @Override
  protected Map<String, ParamItem> defaultParam() {
    Map<String, ParamItem> map = new HashMap<>();
    map.put(STRATEGY_PARAM_QPS, new ParamItem(Double.class, DEFAULT_QPS));
    return map;
  }

  public boolean tryAcquire() {
    return rateLimiter.tryAcquire();
  }

  public boolean acquire() {
    rateLimiter.acquire();
    return true;
  }
```
