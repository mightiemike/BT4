### Title
Unauthenticated CPU exhaustion via unbounded zk-SNARK spend proof generation in `Wallet.createShieldedTransaction` before any fee/spend-count enforcement - ([File: framework/src/main/java/org/tron/core/Wallet.java])

### Summary
`Wallet.createShieldedTransaction(PrivateParameters)` iterates over the attacker-supplied `shielded_spends` list and calls `ZenTransactionBuilder.addSpend`/`generateSpendProof` (via `build()`) for every entry, invoking the expensive `librustzcashSaplingSpendProof` native routine per spend, with no upper bound on list size and no fee charged. The `<=1 spend` cap enforced by `ShieldedTransferActuator.checkSender` only runs later, during actuator `validate()` on broadcast, which this off-chain "create" RPC never reaches.

### Finding Description
`Wallet.createShieldedTransaction` is reachable unauthenticated via gRPC `CreateShieldedTransaction` and HTTP `CreateShieldedTransactionServlet.doPost` [1](#0-0) [2](#0-1) . Only the guard `checkAllowShieldedTransactionApi()` gates it, plus basic field-presence validation; there is no limit on `request.getShieldedSpendsList()` size [3](#0-2) .

For every `SpendNote` in the list, the code constructs an `ExpandedSpendingKey` from the attacker-supplied `ask/nsk/ovk`, decodes the attacker-supplied payment address/voucher, and calls `builder.addSpend(...)` [4](#0-3) . `builder.build()` (not shown in full but confirmed by test usage) subsequently calls `generateSpendProof` per queued spend, which derives `ak`/`nk` via `fullViewingKey()`/`librustzcashNskToNk` and then invokes `JLibrustzcash.librustzcashSaplingSpendProof`, the CPU-heavy zk-SNARK proving routine [5](#0-4) . This is confirmed as the expensive step by the project's own benchmark test, which times a single `generateSpendProof` call in milliseconds [6](#0-5) .

Crucially, `createShieldedTransaction` never invokes `ShieldedTransferActuator` at all — it purely builds and returns a `TransactionCapsule` for the client to sign and broadcast separately. The `<=1 spend` cap in `ShieldedTransferActuator.checkSender` [7](#0-6) , and the fee charged in `execute()`/`calcFee` [8](#0-7) , only apply when/if the resulting transaction is actually broadcast and processed by an actuator. Since the attacker only calls the "create" API and never broadcasts, none of that enforcement is reached, yet the server has already performed N spend-proof computations by the time `createShieldedTransaction` returns (or throws late, e.g. from `checkCmValid`/note decoding after some proofs already ran, depending on ordering — but even in the best case, all N proofs run inside the `for` loop before `builder.build()` returns).

The only mitigations present are: (1) `checkAllowShieldedTransactionApi()` (a deploy-time feature flag, not an authorization control on individual requests) and (2) the generic per-endpoint/per-IP `RateLimiterServlet` QPS throttling on the HTTP path [9](#0-8) , which limits *request rate* but does not limit *work per request* — a single request with N=200 spends still runs 200 proofs under one permit. The gRPC path (`CreateShieldedTransaction` RPC) is not shown to have equivalent per-request cost-based limiting in the reviewed code.

### Impact Explanation
Each spend triggers one native zk-SNARK proof generation, which is measurably CPU-expensive (benchmarked in-repo). An attacker can submit requests with tens to hundreds of fabricated spend notes (self-generated keys/notes, no real value or authorization needed since these are never validated against on-chain nullifiers/merkle roots at this stage) and force the node to burn CPU cycles proportionally, with zero fee debited and no on-chain footprint. Concurrent requests multiply the effect, enabling denial-of-service degradation of the node.

### Likelihood Explanation
Requires `AllowShieldedTransactionApi` (and `AllowShieldedTransaction`) dynamic properties enabled, which is described as default/common deploy configuration for shielded-transaction-capable nodes. No signature, funds, or on-chain state is needed to construct the `PrivateParameters` payload — any `ask/nsk/ovk` and self-generated notes/vouchers pass the code's structural checks in `createShieldedTransaction`, since it doesn't verify voucher membership against actual chain state. This makes the attack straightforward and repeatable by any unauthenticated caller who can reach the HTTP/gRPC endpoint, bounded only by generic connection/QPS limits rather than per-request cost.

### Recommendation
- Enforce a maximum number of `shielded_spends` (and `shielded_receives`) entries in `Wallet.createShieldedTransaction` / `createShieldedTransactionWithoutSpendAuthSig` before any proof generation begins, mirroring the on-chain `<=1 spend` / `<=2 receive` caps enforced by `ShieldedTransferActuator.checkSender`/`checkReceiver`.
- Consider validating spend count immediately after parsing `shieldedSpends` in `Wallet.createShieldedTransaction`, rejecting with `ContractValidateException` before entering the spend-processing loop that triggers proof generation.
- Add request-cost-aware rate limiting (e.g., per-request proof-count-based throttling) rather than relying solely on generic QPS limiters for these shielded-transaction creation endpoints.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/ShieldWalletTest.java (extend)
@Test
public void testCreateShieldedTransactionManySpendsCpuCost() throws Exception {
  int N = 200;
  PrivateParameters.Builder builder = PrivateParameters.newBuilder();
  // Populate transparentFromAddress or ask/nsk/ovk minimally,
  // and N fabricated SpendNote entries built from self-generated
  // SpendingKey.random() notes + IncrementalMerkleVoucherContainer
  // (as done in ShieldedTransferActuatorTest / SendCoinShieldTest helpers),
  // none of which need to correspond to real chain state.
  for (int i = 0; i < N; i++) {
    builder.addShieldedSpends(fabricateSpendNote()); // helper using SpendingKey.random()
  }
  builder.addShieldedReceives(fabricateReceiveNote());

  long start = System.currentTimeMillis();
  try {
    wallet.createShieldedTransaction(builder.build());
  } catch (Exception ignored) {
    // even if it later fails validation, spend proofs for prior entries
    // have already been computed inside the loop
  }
  long elapsed = System.currentTimeMillis() - start;

  // Assert elapsed time scales ~linearly with N and is disproportionate
  // to zero fee charged (createShieldedTransaction charges nothing),
  // e.g. elapsed > N * (single-proof-time-from-LibrustzcashTest.benchmarkCreateSpend) * 0.5
  Assert.assertTrue("CPU cost should be significant and unpriced", elapsed > 1000);
}
```
Combine with concurrent invocation (e.g., `ExecutorService` firing multiple such requests) to demonstrate aggregate CPU saturation, and compare against `LibrustzcashTest.benchmarkCreateSpend` per-proof timings to show the multiplicative, fee-free cost.

### Citations

**File:** protocol/src/main/protos/api/api.proto (L365-367)
```text
  // for shieldedTransaction
  rpc CreateShieldedTransaction (PrivateParameters) returns (TransactionExtention) {
  };
```

**File:** framework/src/main/java/org/tron/core/services/http/CreateShieldedTransactionServlet.java (L24-37)
```java
  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      PrivateParameters.Builder build = PrivateParameters.newBuilder();
      JsonFormat.merge(params.getParams(), build, params.isVisible());

      Transaction tx = wallet
          .createShieldedTransaction(build.build())
          .getInstance();
      response.getWriter().println(Util.printCreateTransaction(tx, params.isVisible()));
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2269-2314)
```java
  public TransactionCapsule createShieldedTransaction(PrivateParameters request)
      throws ContractValidateException, RuntimeException, ZksnarkException, BadItemException {
    checkAllowShieldedTransactionApi();

    ZenTransactionBuilder builder = new ZenTransactionBuilder(this);

    // set timeout
    long timeout = request.getTimeout();
    if (timeout < 0) {
      throw new ContractValidateException("Timeout must >= 0");
    }
    builder.setTimeout(timeout);

    byte[] transparentFromAddress = request.getTransparentFromAddress().toByteArray();
    byte[] ask = request.getAsk().toByteArray();
    byte[] nsk = request.getNsk().toByteArray();
    byte[] ovk = request.getOvk().toByteArray();

    if (ArrayUtils.isEmpty(transparentFromAddress) && (ArrayUtils.isEmpty(ask) || ArrayUtils
        .isEmpty(nsk) || ArrayUtils.isEmpty(ovk))) {
      throw new ContractValidateException("No input address");
    }

    long fromAmount = request.getFromAmount();
    if (!ArrayUtils.isEmpty(transparentFromAddress) && fromAmount <= 0) {
      throw new ContractValidateException("Input amount must > 0");
    }

    List<SpendNote> shieldedSpends = request.getShieldedSpendsList();
    if (!(ArrayUtils.isEmpty(ask) || ArrayUtils.isEmpty(nsk) || ArrayUtils.isEmpty(ovk))
        && shieldedSpends.isEmpty()) {
      throw new ContractValidateException("No input note");
    }

    List<ReceiveNote> shieldedReceives = request.getShieldedReceivesList();
    byte[] transparentToAddress = request.getTransparentToAddress().toByteArray();
    if (shieldedReceives.isEmpty() && ArrayUtils.isEmpty(transparentToAddress)) {
      throw new ContractValidateException("No output address");
    }

    long toAmount = request.getToAmount();
    if (!ArrayUtils.isEmpty(transparentToAddress) && toAmount <= 0) {
      throw new ContractValidateException("Output amount must > 0");
    }

    checkCmValid(shieldedSpends, shieldedReceives);
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2336-2356)
```java
      if (!(ArrayUtils.isEmpty(ask) || ArrayUtils.isEmpty(nsk) || ArrayUtils.isEmpty(ovk))) {
        ExpandedSpendingKey expsk = new ExpandedSpendingKey(ask, nsk, ovk);
        for (SpendNote spendNote : shieldedSpends) {
          GrpcAPI.Note note = spendNote.getNote();
          PaymentAddress paymentAddress = KeyIo.decodePaymentAddress(note.getPaymentAddress());
          if (paymentAddress == null) {
            throw new ZksnarkException(PAYMENT_ADDRESS_FORMAT_WRONG);
          }
          Note baseNote = new Note(paymentAddress.getD(),
              paymentAddress.getPkD(), note.getValue(), note.getRcm().toByteArray());

          IncrementalMerkleVoucherContainer voucherContainer =
              new IncrementalMerkleVoucherCapsule(
                  spendNote.getVoucher()).toMerkleVoucherContainer();
          builder.addSpend(expsk,
              baseNote,
              spendNote.getAlpha().toByteArray(),
              spendNote.getVoucher().getRt().toByteArray(),
              voucherContainer);
        }
      }
```

**File:** framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java (L210-254)
```java
  // Note: should call librustzcashSaplingProvingCtxFree in the caller
  public SpendDescriptionCapsule generateSpendProof(SpendDescriptionInfo spend,
      long ctx) throws ZksnarkException {

    byte[] cm = spend.note.cm();

    // check if ak exists
    byte[] ak;
    byte[] nf;
    byte[] nsk;
    if (!ArrayUtils.isEmpty(spend.ak)) {
      ak = spend.ak;
      nf = spend.note.nullifier(ak, JLibrustzcash.librustzcashNskToNk(spend.nsk),
          spend.voucher.position());
      nsk = spend.nsk;
    } else {
      ak = spend.expsk.fullViewingKey().getAk();
      nf = spend.note.nullifier(spend.expsk.fullViewingKey(), spend.voucher.position());
      nsk = spend.expsk.getNsk();
    }

    if (ByteArray.isEmpty(cm) || ByteArray.isEmpty(nf)) {
      throw new ZksnarkException("Spend is invalid");
    }

    byte[] voucherPath = spend.voucher.path().encode();

    byte[] cv = new byte[32];
    byte[] rk = new byte[32];
    byte[] zkproof = new byte[192];
    if (!JLibrustzcash.librustzcashSaplingSpendProof(
        new SpendProofParams(ctx,
            ak,
            nsk,
            spend.note.getD().getData(),
            spend.note.getRcm(),
            spend.alpha,
            spend.note.getValue(),
            spend.anchor,
            voucherPath,
            cv,
            rk,
            zkproof))) {
      throw new ZksnarkException("Spend proof failed");
    }
```

**File:** framework/src/test/java/org/tron/core/zksnark/LibrustzcashTest.java (L331-368)
```java
  public long benchmarkCreateSaplingSpend() throws BadItemException, ZksnarkException {

    long startTime = System.currentTimeMillis();

    ZenTransactionBuilder builder = new ZenTransactionBuilder(wallet);

    SpendingKey spendingKey = SpendingKey.random();
    ExpandedSpendingKey expsk = spendingKey.expandedSpendingKey();
    PaymentAddress address = spendingKey.defaultAddress();

    long value = randomInt(100, 100000);
    Note note = new Note(address, value);
    byte[] cm = note.cm();

    IncrementalMerkleTreeContainer tree =
        new IncrementalMerkleTreeContainer(new IncrementalMerkleTreeCapsule());
    PedersenHashCapsule compressCapsule1 = new PedersenHashCapsule();
    compressCapsule1.setContent(ByteString.copyFrom(cm));
    PedersenHash a = compressCapsule1.getInstance();
    tree.append(a);
    IncrementalMerkleVoucherContainer voucher = tree.toVoucher();

    byte[] anchor = voucher.root().getContent().toByteArray();

    SpendDescriptionInfo spend = new SpendDescriptionInfo(expsk, note, anchor, voucher);

    long proofContext = JLibrustzcash.librustzcashSaplingProvingCtxInit();
    SpendDescriptionCapsule spendDescriptionCapsule = builder
        .generateSpendProof(spend, proofContext);
    JLibrustzcash.librustzcashSaplingProvingCtxFree(proofContext);

    long endTime = System.currentTimeMillis();
    long time = endTime - startTime;
    System.out.println("time is: " + time + "ms, result is: " + ByteArray
        .toHexString(spendDescriptionCapsule.getData()));

    return time;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L55-111)
```java
  @Override
  public boolean execute(Object result)
      throws ContractExeException {
    TransactionResultCapsule ret = (TransactionResultCapsule) result;
    if (Objects.isNull(ret)) {
      throw new RuntimeException(ActuatorConstant.TX_RESULT_NULL);
    }

    AccountStore accountStore = chainBaseManager.getAccountStore();
    AssetIssueStore assetIssueStore = chainBaseManager.getAssetIssueStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    try {
      shieldedTransferContract = any.unpack(ShieldedTransferContract.class);
    } catch (InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractExeException(e.getMessage());
    }

    long fee = calcFee(shieldedTransferContract);
    try {
      if (shieldedTransferContract.getTransparentFromAddress().toByteArray().length > 0) {
        executeTransparentFrom(shieldedTransferContract.getTransparentFromAddress().toByteArray(),
            shieldedTransferContract.getFromAmount(), ret, fee);
      }
      Commons.adjustAssetBalanceV2(accountStore.getBlackhole(),
          CommonParameter.getInstance().getZenTokenId(), fee,
          accountStore, assetIssueStore, dynamicStore);
    } catch (BalanceInsufficientException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(0, code.FAILED);
      ret.setShieldedTransactionFee(fee);
      throw new ContractExeException(e.getMessage());
    }

    executeShielded(shieldedTransferContract.getSpendDescriptionList(),
        shieldedTransferContract.getReceiveDescriptionList(), ret, fee);

    if (shieldedTransferContract.getTransparentToAddress().toByteArray().length > 0) {
      executeTransparentTo(shieldedTransferContract.getTransparentToAddress().toByteArray(),
          shieldedTransferContract.getToAmount(), ret, fee);
    }

    //adjust and verify total shielded pool value
    try {
      Commons.adjustTotalShieldedPoolValue(addExact(subtractExact(
          shieldedTransferContract.getToAmount(),
          shieldedTransferContract.getFromAmount()), fee), dynamicStore);
    } catch (ArithmeticException | BalanceInsufficientException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(0, code.FAILED);
      ret.setShieldedTransactionFee(fee);
      throw new ContractExeException(e.getMessage());
    }

    ret.setStatus(0, code.SUCESS);
    ret.setShieldedTransactionFee(fee);
    return true;
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L366-380)
```java
  private void checkSender(ShieldedTransferContract shieldedTransferContract)
      throws ContractValidateException {
    if (!shieldedTransferContract.getTransparentFromAddress().isEmpty()
        && shieldedTransferContract.getSpendDescriptionCount() > 0) {
      throw new ContractValidateException("ShieldedTransferContract error, more than 1 senders");
    }
    if (shieldedTransferContract.getTransparentFromAddress().isEmpty()
        && shieldedTransferContract.getSpendDescriptionCount() == 0) {
      throw new ContractValidateException("ShieldedTransferContract error, no sender");
    }
    if (shieldedTransferContract.getSpendDescriptionCount() > 1) {
      throw new ContractValidateException("ShieldedTransferContract error, number of spend notes"
          + " should not be more than 1");
    }
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
