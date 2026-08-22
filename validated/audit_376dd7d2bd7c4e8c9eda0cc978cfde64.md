### Title
Unauthenticated CPU-exhaustion DoS via `ScanNoteByIvk` wide-range block scanning bypassing per-endpoint QPS metering - ([File: framework/src/main/java/org/tron/core/Wallet.java])

### Summary
`Wallet.queryNoteByIvk` (framework/src/main/java/org/tron/core/Wallet.java:3359-3421), reachable via the gRPC `ScanNoteByIvk`/`ScanAndMarkNoteByIvk` endpoints, allows any unauthenticated caller to force the node to run zk-SNARK `Note.decrypt` (ECC operations) against every `ReceiveDescription` of every `ShieldedTransferContract` in up to 1000 blocks per call, with no cost accounting beyond a flat per-endpoint QPS limiter. When the target is a full node (or a lite node with `openHistoryQueryWhenLiteFN=true`), `LiteFnQueryGrpcInterceptor` does not block the call at all, so this expensive computation is gated only by `RateLimiterInterceptor`'s QPS/global-permit check, not by actual CPU cost, enabling sustained CPU exhaustion with concurrent callers.

### Finding Description
`queryNoteByIvk` only enforces a range-size bound (`endNum - startNum <= 1000`) at framework/src/main/java/org/tron/core/Wallet.java:3362-3365, then iterates every block in that window, every transaction, and every `ReceiveDescription` of `ShieldedTransferContract`s, calling `Note.decrypt(...)` (framework/src/main/java/org/tron/core/Wallet.java:3387-3393) for each one regardless of whether `ivk` is valid. `Note.decrypt` performs zk-snark elliptic-curve decryption work per note, so the CPU cost of a single call scales with the number of shielded outputs across up to 1000 blocks — a value the caller does not pay for in energy/bandwidth because this is a read-only gRPC query, not a transaction.

The `LiteFnQueryGrpcInterceptor.interceptCall` (framework/src/main/java/org/tron/core/services/filter/LiteFnQueryGrpcInterceptor.java:79-91) only blocks `protocol.Wallet/ScanNoteByIvk` when `chainBaseManager.isLiteNode()` is true AND `openHistoryQueryWhenLiteFN` is false. On a full node, or a lite node explicitly configured to allow history queries, this filter is entirely bypassed and the request reaches `Wallet.scanNoteByIvk` → `queryNoteByIvk` unimpeded.

The only remaining gate is `RateLimiterInterceptor` (framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java:95-163), which enforces a QPS-based permit per method name (`DefaultBaseQqsAdapter` with `QpsStrategy.DEFAULT_QPS_PARAM`) plus a global permit. This is a request-count limiter, not a cost-aware limiter — it does not account for how many blocks/notes a given `ScanNoteByIvk` request will scan. A caller that stays under the QPS threshold (e.g., issuing requests from multiple parallel connections/IPs, or simply slow enough to stay under QPS but each maximally expensive) can still saturate CPU with decrypt operations, because each individual call is "cheap" from the rate limiter's perspective while being CPU-heavy in `Note.decrypt` execution.

No authentication, no shielded key possession, and no fee/energy payment are required — `ScanNoteByIvk` is a plaintext gRPC read call reachable by any unprivileged client with network access to the gRPC port (default-enabled service).

### Impact Explanation
This is a DoS via RPC-API: an unprivileged client can force the node's RPC-serving threads/CPU to be consumed by cryptographic decrypt operations across up to 1000 blocks per call, with garbage `ivk` values (decrypt attempts still execute the same cost whether or not they succeed). Repeated/concurrent invocation across parallel channels can degrade responsiveness of other RPC handlers (e.g., `GetNowBlock`), matching the "DoS via RPC-API" bounty impact class. The impact is scoped to RPC availability/latency degradation, not to consensus, funds, or key material.

### Likelihood Explanation
- Preconditions: gRPC port reachable, target is a full node OR a lite node with `openHistoryQueryWhenLiteFN=true` (both are legitimate deployment configurations, not attacker-controlled misconfiguration in the sense of being unusual — many mainnet full nodes run with default full-node config, which is the common non-lite deployment).
- Cost to attacker: zero economic cost — no transaction, no fee, no energy/bandwidth consumption; only network requests.
- Feasibility: trivial to script — open N parallel gRPC channels and repeatedly call `ScanNoteByIvk` with `startNum=0, endNum=999` and random `ivk`.
- Repeatability: fully repeatable, bounded only by the flat per-endpoint QPS limiter, which does not scale down with the actual cost of the request.

### Recommendation
Add cost-aware throttling for shielded-scan RPCs: e.g., limit total notes/`ReceiveDescription`s processed per unit time per client/IP (not just request count), reduce the default maximum scan window for public/unauthenticated access, require the caller to supply a valid, previously-registered viewing key context, or move expensive decrypt work to a bounded worker pool separate from the general RPC executor so it cannot starve other handlers. Consider making `ScanNoteByIvk`/`ScanNoteByOvk`/`ScanShieldedTRC20NotesByIvk` subject to a stricter `IPQPSRateLimiterAdapter` or `GlobalPreemptibleAdapter` configuration by default, and/or applying an actual CPU-time budget check inside `queryNoteByIvk`.

### Proof of Concept
```
// Pseudo load-test (JUnit-style) illustrating the exploit path
GrpcAPI.NoteParameters req = ScanNoteByIvk.newBuilder()
    .setStartNum(0)
    .setEndNum(999)
    .setIvk(ByteString.copyFrom(randomBytes(32))) // garbage ivk, no key ownership needed
    .build();

// Open N parallel channels (unauthenticated)
for (int i = 0; i < N; i++) {
  new Thread(() -> {
    while (true) {
      walletStub.scanNoteByIvk(req); // triggers queryNoteByIvk -> Note.decrypt loop over up to 1000 blocks
    }
  }).start();
}

// Concurrently measure baseline RPC latency
long p99Before = measureP99(() -> walletStub.getNowBlock(EmptyMessage.newBuilder().build()));
// ... start flood ...
long p99During = measureP99(() -> walletStub.getNowBlock(EmptyMessage.newBuilder().build()));

Assert.assertTrue("Expected P99 latency of unrelated RPC to degrade under ScanNoteByIvk flood",
    p99During > p99Before * K); // demonstrates CPU starvation despite passing per-endpoint QPS checks
```
Expected result: `GetNowBlock` P99 latency and/or server thread-pool saturation increases significantly while `ScanNoteByIvk` floods, even though each individual `ScanNoteByIvk` call respects `RateLimiterInterceptor`'s per-endpoint QPS limit, confirming the metering gap identified in `Wallet.queryNoteByIvk` (framework/src/main/java/org/tron/core/Wallet.java:3359-3421). [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L3359-3421)
```java
  private GrpcAPI.DecryptNotes queryNoteByIvk(long startNum, long endNum, byte[] ivk)
      throws BadItemException, ZksnarkException {
    GrpcAPI.DecryptNotes.Builder builder = GrpcAPI.DecryptNotes.newBuilder();
    if (!(startNum >= 0 && endNum > startNum && endNum - startNum <= 1000)) {
      throw new BadItemException(
          SHIELDED_TRANSACTION_SCAN_RANGE);
    }
    BlockList blockList = this.getBlocksByLimitNext(startNum, endNum - startNum);
    for (Block block : blockList.getBlockList()) {
      for (Transaction transaction : block.getTransactionsList()) {
        TransactionCapsule transactionCapsule = new TransactionCapsule(transaction);
        byte[] txid = transactionCapsule.getTransactionId().getBytes();
        List<Transaction.Contract> contracts = transaction.getRawData().getContractList();
        if (contracts.isEmpty()) {
          continue;
        }
        Transaction.Contract c = contracts.get(0);
        if (c.getType() != Contract.ContractType.ShieldedTransferContract) {
          continue;
        }
        ShieldedTransferContract stContract;
        try {
          stContract = c.getParameter().unpack(ShieldedTransferContract.class);
        } catch (InvalidProtocolBufferException e) {
          throw new ZksnarkException(
              "unpack ShieldedTransferContract failed.");
        }

        for (int index = 0; index < stContract.getReceiveDescriptionList().size(); index++) {
          ReceiveDescription r = stContract.getReceiveDescription(index);
          Optional<Note> notePlaintext = Note.decrypt(r.getCEnc().toByteArray(),//ciphertext
              ivk,
              r.getEpk().toByteArray(),//epk
              r.getNoteCommitment().toByteArray() //cmu
          );

          if (notePlaintext.isPresent()) {
            Note noteText = notePlaintext.get();
            byte[] pkD = new byte[32];
            if (!JLibrustzcash
                .librustzcashIvkToPkd(new IvkToPkdParams(ivk, noteText.getD().getData(),
                    pkD))) {
              continue;
            }

            String paymentAddress = KeyIo
                .encodePaymentAddress(new PaymentAddress(noteText.getD(), pkD));
            GrpcAPI.Note note = GrpcAPI.Note.newBuilder()
                .setPaymentAddress(paymentAddress)
                .setValue(noteText.getValue())
                .setRcm(ByteString.copyFrom(noteText.getRcm()))
                .setMemo(ByteString.copyFrom(stripRightZero(noteText.getMemo())))
                .build();
            DecryptNotes.NoteTx noteTx = DecryptNotes.NoteTx.newBuilder().setNote(note)
                .setTxid(ByteString.copyFrom(txid)).setIndex(index).build();

            builder.addNoteTxs(noteTx);
          }
        } // end of ReceiveDescriptionList
      } // end of transaction
    } //end of block list
    return builder.build();
  }
```

**File:** framework/src/main/java/org/tron/core/services/filter/LiteFnQueryGrpcInterceptor.java (L79-91)
```java
  @Override
  public <ReqT, RespT> ServerCall.Listener<ReqT> interceptCall(ServerCall<ReqT, RespT> call,
      Metadata headers, ServerCallHandler<ReqT, RespT> next) {
    if (chainBaseManager.isLiteNode()
            && !CommonParameter.getInstance().openHistoryQueryWhenLiteFN
            && filterMethods.contains(call.getMethodDescriptor().getFullMethodName())) {
      call.close(Status.UNAVAILABLE
              .withDescription("this API is closed because this node is a lite fullnode"), headers);
      return new ServerCall.Listener<ReqT>() {};
    } else {
      return next.startCall(call, headers);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java (L95-123)
```java
  @Override
  public <ReqT, RespT> Listener<ReqT> interceptCall(ServerCall<ReqT, RespT> call, Metadata headers,
      ServerCallHandler<ReqT, RespT> next) {

    String methodMeterName = MetricsKey.NET_API_DETAIL_QPS
        + call.getMethodDescriptor().getFullMethodName();
    MetricsUtil.meterMark(MetricsKey.NET_API_QPS);
    MetricsUtil.meterMark(methodMeterName);

    IRateLimiter rateLimiter = container
        .get(KEY_PREFIX_RPC, call.getMethodDescriptor().getFullMethodName());

    Listener<ReqT> listener = new ServerCall.Listener<ReqT>() {};

    RuntimeData runtimeData = new RuntimeData(call);
    // Check per-endpoint first to avoid consuming global IP/QPS quota for requests
    // that would be rejected by the per-endpoint limiter anyway. acquirePermit()
    // chooses blocking or non-blocking semantics based on rate.limiter.apiNonBlocking.
    boolean perEndpointAcquired = rateLimiter == null || rateLimiter.acquirePermit(runtimeData);
    boolean acquireResource = perEndpointAcquired && GlobalRateLimiter.acquirePermit(runtimeData);

    if (!acquireResource) {
      // Release the per-endpoint permit when global rejected, to avoid semaphore leak.
      if (rateLimiter instanceof IPreemptibleRateLimiter && perEndpointAcquired) {
        ((IPreemptibleRateLimiter) rateLimiter).release();
      }
      call.close(Status.fromCode(Code.RESOURCE_EXHAUSTED), new Metadata());
      return listener;
    }
```
