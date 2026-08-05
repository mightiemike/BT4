### Title
Unauthenticated/fee-less forced zk-SNARK spend+output proof generation via `Wallet.createShieldedTransaction` (and `...WithoutSpendAuthSig`) - ([File: framework/src/main/java/org/tron/core/Wallet.java])

### Summary
`Wallet.createShieldedTransaction` and `Wallet.createShieldedTransactionWithoutSpendAuthSig`, exposed as public gRPC/HTTP endpoints, build a full Sapling `ZenTransactionBuilder` transaction — including a Groth16 spend proof and output proof via `ZenTransactionBuilder.build()` — for any caller-supplied `shieldedSpends`/`ovk`/`ask`/`nsk` data, without verifying the spend note or Merkle voucher against real on-chain state and without charging the `shieldedTransactionFee`. A `transparentToAddress`-only request (shielded → public conversion) with no explicit receive additionally triggers `createReceiveNoteRandom`, but the dominant, uncapped cost is the mandatory spend+output zk-proof generation performed on arbitrary attacker-fabricated inputs.

### Finding Description
`Wallet.createShieldedTransaction` (`framework/src/main/java/org/tron/core/Wallet.java:2269-2368`) only performs structural/value checks via `checkCmValid`/`checkCmNumber`/`checkCmValue` (`Wallet.java:2225-2255`), which validate spend/receive counts (≤1 spend, ≤2 receives) and that note values are non-negative — they never verify that a `SpendNote`'s Merkle `voucher`/`path`/`rt` corresponds to any real leaf/root in the actual chain Merkle tree, nor that `ask`/`nsk`/`ovk` correspond to a genuinely-owned note.

When `shieldedSpends` is non-empty and `transparentToAddress` is set with no explicit `shieldedReceives`, the code auto-generates a random shielded receive via `createReceiveNoteRandom` (`Wallet.java:2257-2267`, 2326-2333). Then, regardless of receive path, `builder.addSpend(...)` is called with the raw, unverified spend material, and `builder.build()` is invoked (`Wallet.java:2361`), which unconditionally calls `generateSpendProof` and `generateOutputProof` for every spend/receive in `ZenTransactionBuilder.build()` (`framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java:138-195`). `generateSpendProof` (`ZenTransactionBuilder.java:211-262`) and `generateOutputProof` (`ZenTransactionBuilder.java:265-313`) call into native `JLibrustzcash` Groth16 proving functions (`librustzcashSaplingSpendProof`, `librustzcashSaplingOutputProof`) using whatever `ak`/`nsk`/`note`/`alpha`/`anchor`/`voucherPath` bytes the caller supplied — these native calls do not check the note/voucher against real chain state; on-chain voucher/anchor legitimacy is only checked later, during `ShieldedTransferActuator` validation of an actually-broadcast transaction (`actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java`), which never runs for a `createShieldedTransaction` call that is only used to build (not broadcast/execute) a transaction.

Crucially, `calcFee`/`getShieldedTransactionFee` enforcement (`ShieldedTransferActuator.java:478-489`, `Wallet.java:2221-2223`) is an on-chain execution-time cost that is entirely bypassed by simply calling the build-only wallet API and never submitting the resulting transaction. The gRPC entrypoints `RpcApiService.createShieldedTransaction` / `createShieldedTransactionWithoutSpendAuthSig` (`framework/src/main/java/org/tron/core/services/RpcApiService.java:2093-2149`) directly forward attacker-controlled `PrivateParameters`/`PrivateParametersWithoutAsk` to these Wallet methods with no fee/ownership pre-check. The only gate is `checkAllowShieldedTransactionApi()`, a boolean config toggle (`ALLOW_SHIELDED_TRANSACTION_API`), not an authentication or payment mechanism — any client permitted to reach the node's gRPC/HTTP API when this flag is enabled can invoke it repeatedly.

The sole mitigating control found is the generic, method-agnostic gRPC rate limiter (`RateLimiterInterceptor`, `framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java`), which applies a default `QpsRateLimiterAdapter` (default 1000 QPS, per `common/src/main/resources/reference.conf:453-521`) to every gRPC method including `CreateShieldedTransaction`, unless overridden. This is a blunt, uniform ceiling unrelated to the actual (expensive, native Groth16) cost of the specific call, not a work-proportional fee or per-caller-cost accounting mechanism.

### Impact Explanation
Any client able to reach the node's shielded gRPC/HTTP API (when `ALLOW_SHIELDED_TRANSACTION_API` is enabled, a standard config for full nodes supporting shielded functionality) can force that node to perform expensive native Groth16 spend-proof and output-proof computations on entirely fabricated `SpendNote`/`ReceiveNote`/`ask`/`nsk`/`ovk` data, with zero economic cost (no `shieldedTransactionFee` charged, since fee accounting only happens in `ShieldedTransferActuator.execute` for a transaction that is actually broadcast and mined). Up to the default rate-limiter ceiling (1000 QPS per gRPC method, configurable), this allows sustained CPU exhaustion of the serving node's shielded-transaction-building subsystem, degrading the node's ability to service legitimate wallet/API requests.

### Likelihood Explanation
- Preconditions: the target node must have `ALLOW_SHIELDED_TRANSACTION_API` enabled (a common, documented full-node setting for exposing shielded wallet-building APIs) and expose the gRPC/HTTP endpoint to the attacker.
- No real shielded funds, valid Merkle voucher, or spend-authority ownership is required — arbitrary/garbage `ask`/`nsk`/`ovk`/note/voucher bytes pass the pre-build checks (`checkCmValid`) and reach `generateSpendProof`/`generateOutputProof`.
- Repeatable at the node's configured per-method QPS ceiling (default 1000 QPS) with no per-request fee, since the call never needs to be submitted on-chain to incur the CPU cost — the cost is paid entirely by the node at request time.

### Recommendation
- Require and verify a real, matching on-chain Merkle voucher/anchor (i.e., confirm the `rt`/`anchor` exists in `MerkleContainer`) before performing spend proof generation in `createShieldedTransaction`/`createShieldedTransactionWithoutSpendAuthSig`, rejecting requests with unknown/invalid anchors before invoking `ZenTransactionBuilder.build()`.
- Apply a dedicated, work-proportional rate limit (e.g., `IPQPSRateLimiterAdapter` with a low QPS, or a `GlobalPreemptibleAdapter` concurrency cap) specifically to `CreateShieldedTransaction`/`CreateShieldedTransactionWithoutSpendAuthSig` in `reference.conf`, rather than relying on the generic 1000 QPS default.
- Consider requiring authentication/API keys or restricting these build-only, proof-generating endpoints to trusted/local callers by default.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/zksnark/ShieldedSpendDosTest.java
@Test
public void testUnverifiedSpendForcesExpensiveProofGeneration() throws Exception {
  chainBaseManager.getDynamicPropertiesStore().saveAllowShieldedTransaction(1);

  // Fabricate spend material with NO corresponding real Merkle tree entry.
  SpendingKey fakeSk = SpendingKey.random();
  ExpandedSpendingKey fakeExpsk = fakeSk.expandedSpendingKey();
  PaymentAddress fakeAddr = fakeSk.defaultAddress();
  Note fakeNote = new Note(fakeAddr, 100 * 1000000L);
  IncrementalMerkleVoucherContainer fakeVoucher =
      createSimpleMerkleVoucherContainer(fakeNote.cm()); // never stored via MerkleContainer.putMerkleTreeIntoStore
  byte[] fakeAnchor = fakeVoucher.root().getContent().toByteArray();

  GrpcAPI.SpendNote.Builder spendNoteBuilder = GrpcAPI.SpendNote.newBuilder();
  // ... populate note/alpha/voucher/path fields with fakeNote/fakeVoucher/fakeAnchor ...

  PrivateParameters.Builder req = PrivateParameters.newBuilder()
      .setAsk(ByteString.copyFrom(fakeExpsk.getAsk()))
      .setNsk(ByteString.copyFrom(fakeExpsk.getNsk()))
      .setOvk(ByteString.copyFrom(fakeExpsk.getOvk()))
      .addShieldedSpends(spendNoteBuilder.build())
      .setTransparentToAddress(ByteString.copyFrom(toAddress))
      .setToAmount(1L);
      // shieldedReceives left empty -> triggers createReceiveNoteRandom path

  long start = System.nanoTime();
  for (int i = 0; i < 500; i++) {
    // Each call performs full Groth16 spend+output proof generation with NO fee
    // and NO validation that fakeAnchor/fakeVoucher exist on-chain.
    TransactionCapsule tx = wallet.createShieldedTransaction(req.build());
    Assert.assertNotNull(tx); // build succeeds despite fabricated/non-existent Merkle data
  }
  long elapsedMs = (System.nanoTime() - start) / 1_000_000;
  // Assert this consumes disproportionate CPU time given zero fee/ownership was required.
  System.out.println("500 forged-spend proof builds took " + elapsedMs + " ms");
}
```
Expected result: the loop completes successfully (transaction capsules are built) despite `fakeAnchor`/`fakeVoucher` never being registered in `chainBaseManager.getMerkleContainer()`, demonstrating that `createShieldedTransaction` performs full Groth16 proof generation on unverified, attacker-controlled data with no fee or ownership check — confirming the underpriced-CPU-work condition. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2257-2267)
```java
  public ReceiveNote createReceiveNoteRandom(long value) throws ZksnarkException, BadItemException {
    SpendingKey spendingKey = SpendingKey.random();
    PaymentAddress paymentAddress = spendingKey.defaultAddress();

    GrpcAPI.Note note = GrpcAPI.Note.newBuilder().setValue(value)
        .setPaymentAddress(KeyIo.encodePaymentAddress(paymentAddress))
        .setRcm(ByteString.copyFrom(Note.generateR()))
        .setMemo(ByteString.copyFrom(new byte[512])).build();

    return ReceiveNote.newBuilder().setNote(note).build();
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2314-2334)
```java
    checkCmValid(shieldedSpends, shieldedReceives);

    try {
      // add
      if (!ArrayUtils.isEmpty(transparentFromAddress)) {
        builder.setTransparentInput(transparentFromAddress, fromAmount);
      }

      if (!ArrayUtils.isEmpty(transparentToAddress)) {
        builder.setTransparentOutput(transparentToAddress, toAmount);
      }

      // from shielded to public, without shielded receive, will create a random shielded address
      if (!shieldedSpends.isEmpty()
          && !ArrayUtils.isEmpty(transparentToAddress)
          && shieldedReceives.isEmpty()) {
        shieldedReceives = new ArrayList<>();
        ReceiveNote receiveNote = createReceiveNoteRandom(0);
        shieldedReceives.add(receiveNote);
      }

```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2358-2367)
```java
      // output
      shieldedOutput(shieldedReceives, builder, ovk);

      return builder.build();
    } catch (ArithmeticException e) {
      throw new ZksnarkException("shielded amount overflow", e);
    } catch (ZksnarkException e) {
      logger.error("createShieldedTransaction except, error is {}", e.toString());
      throw e;
    }
```

**File:** framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java (L138-195)
```java
  public TransactionCapsule build(boolean withAsk) throws ZksnarkException {
    TransactionCapsule transactionCapsule;
    long ctx = JLibrustzcash.librustzcashSaplingProvingCtxInit();

    try {
      // Create SpendDescriptions
      for (SpendDescriptionInfo spend : spends) {
        SpendDescriptionCapsule spendDescriptionCapsule = generateSpendProof(spend, ctx);
        contractBuilder.addSpendDescription(spendDescriptionCapsule.getInstance());
      }

      // Create OutputDescriptions
      for (ReceiveDescriptionInfo receive : receives) {
        ReceiveDescriptionCapsule receiveDescriptionCapsule = generateOutputProof(receive, ctx);
        contractBuilder.addReceiveDescription(receiveDescriptionCapsule.getInstance());
      }

      // Empty output script
      byte[] dataHashToBeSigned; //256
      transactionCapsule = wallet.createTransactionCapsuleWithoutValidate(
          contractBuilder.build(), ContractType.ShieldedTransferContract, timeout);

      dataHashToBeSigned = TransactionCapsule
          .getShieldTransactionHashIgnoreTypeException(transactionCapsule.getInstance());

      if (dataHashToBeSigned == null) {
        throw new ZksnarkException("cal transaction hash failed");
      }

      // Create spendAuth and binding signatures
      if (withAsk) {
        createSpendAuth(dataHashToBeSigned);
      }

      byte[] bindingSig = new byte[64];
      JLibrustzcash.librustzcashSaplingBindingSig(
          new BindingSigParams(ctx,
              valueBalance,
              dataHashToBeSigned,
              bindingSig)
      );
      contractBuilder.setBindingSignature(ByteString.copyFrom(bindingSig));
    } catch (ZksnarkException e) {
      throw e;
    } finally {
      JLibrustzcash.librustzcashSaplingProvingCtxFree(ctx);
    }
    Transaction.raw.Builder rawBuilder = transactionCapsule.getInstance().toBuilder()
        .getRawDataBuilder()
        .clearContract()
        .addContract(
            Transaction.Contract.newBuilder().setType(ContractType.ShieldedTransferContract)
                .setParameter(
                    Any.pack(contractBuilder.build())).build());
    Transaction transaction = transactionCapsule.getInstance().toBuilder().clearRawData()
        .setRawData(rawBuilder).build();
    return new TransactionCapsule(transaction);
  }
```

**File:** framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java (L211-313)
```java
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
    SpendDescriptionCapsule spendDescriptionCapsule = new SpendDescriptionCapsule();
    spendDescriptionCapsule.setValueCommitment(cv);
    spendDescriptionCapsule.setRk(rk);
    spendDescriptionCapsule.setZkproof(zkproof);
    spendDescriptionCapsule.setAnchor(spend.anchor);
    spendDescriptionCapsule.setNullifier(nf);
    return spendDescriptionCapsule;
  }

  // Note: should call librustzcashSaplingProvingCtxFree in the caller
  public ReceiveDescriptionCapsule generateOutputProof(ReceiveDescriptionInfo output, long ctx)
      throws ZksnarkException {
    byte[] cm = output.getNote().cm();
    if (ByteArray.isEmpty(cm)) {
      throw new ZksnarkException("Output is invalid");
    }

    Optional<NotePlaintextEncryptionResult> res = output.getNote()
        .encrypt(output.getNote().getPkD());
    if (!res.isPresent()) {
      throw new ZksnarkException("Failed to encrypt note");
    }

    NotePlaintextEncryptionResult enc = res.get();
    NoteEncryption encryptor = enc.getNoteEncryption();

    byte[] cv = new byte[32];
    byte[] zkProof = new byte[192];
    if (!JLibrustzcash.librustzcashSaplingOutputProof(
        new OutputProofParams(ctx,
            encryptor.getEsk(),
            output.getNote().getD().getData(),
            output.getNote().getPkD(),
            output.getNote().getRcm(),
            output.getNote().getValue(),
            cv,
            zkProof))) {
      throw new ZksnarkException("Output proof failed");
    }

    if (ArrayUtils.isEmpty(output.ovk) || output.ovk.length != 32) {
      throw new ZksnarkException("ovk is null or invalid and ovk should be 32 bytes (256 bit)");
    }

    ReceiveDescriptionCapsule receiveDescriptionCapsule = new ReceiveDescriptionCapsule();
    receiveDescriptionCapsule.setValueCommitment(cv);
    receiveDescriptionCapsule.setNoteCommitment(cm);
    receiveDescriptionCapsule.setEpk(encryptor.getEpk());
    receiveDescriptionCapsule.setCEnc(enc.getEncCiphertext());
    receiveDescriptionCapsule.setZkproof(zkProof);

    OutgoingPlaintext outPlaintext =
        new OutgoingPlaintext(output.getNote().getPkD(), encryptor.getEsk());
    receiveDescriptionCapsule.setCOut(outPlaintext
        .encrypt(output.ovk, receiveDescriptionCapsule.getValueCommitment().toByteArray(),
            receiveDescriptionCapsule.getCm().toByteArray(),
            encryptor).getData());
    return receiveDescriptionCapsule;
  }
```

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L2093-2120)
```java
    @Override
    public void createShieldedTransaction(PrivateParameters request,
        StreamObserver<TransactionExtention> responseObserver) {

      TransactionExtention.Builder trxExtBuilder = TransactionExtention.newBuilder();
      Return.Builder retBuilder = Return.newBuilder();

      try {
        TransactionCapsule trx = wallet.createShieldedTransaction(request);
        trxExtBuilder.setTransaction(trx.getInstance());
        trxExtBuilder.setTxid(trx.getTransactionId().getByteString());
        retBuilder.setResult(true).setCode(response_code.SUCCESS);
      } catch (ContractValidateException | ZksnarkException e) {
        retBuilder.setResult(false).setCode(response_code.CONTRACT_VALIDATE_ERROR)
            .setMessage(ByteString
                .copyFromUtf8(Wallet.CONTRACT_VALIDATE_ERROR + e.getMessage()));
        logger.debug(CONTRACT_VALIDATE_EXCEPTION, e.getMessage());
      } catch (Exception e) {
        retBuilder.setResult(false).setCode(response_code.OTHER_ERROR)
            .setMessage(ByteString.copyFromUtf8(e.getClass() + " : " + e.getMessage()));
        logger.info("createShieldedTransaction exception caught: " + e.getMessage());
      }

      trxExtBuilder.setResult(retBuilder);
      responseObserver.onNext(trxExtBuilder.build());
      responseObserver.onCompleted();

    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L478-489)
```java
  private long calcFee(ShieldedTransferContract shieldedTransferContract) {
    byte[] toAddress = shieldedTransferContract.getTransparentToAddress().toByteArray();
    boolean hasTransparentTo = (toAddress.length > 0);
    if (hasTransparentTo) {
      AccountCapsule toAccount = chainBaseManager.getAccountStore().get(toAddress);
      if (toAccount == null) {
        return chainBaseManager.getDynamicPropertiesStore()
            .getShieldedTransactionCreateAccountFee();
      }
    }
    return chainBaseManager.getDynamicPropertiesStore().getShieldedTransactionFee();
  }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java (L36-43)
```java
  public void init(Server server) {
    // add default
    for (ServerServiceDefinition service : server.getServices()) {
      for (ServerMethodDefinition<?, ?> method : service.getMethods()) {
        container.add(KEY_PREFIX_RPC, method.getMethodDescriptor().getFullMethodName(),
            new DefaultBaseQqsAdapter(QpsStrategy.DEFAULT_QPS_PARAM));
      }
    }
```

**File:** common/src/main/resources/reference.conf (L453-521)
```text
## Rate limiter config
rate.limiter = {
  # Each HTTP servlet and gRPC method can have its own rate-limit strategy.
  # Three API rate-limit strategies are available:
  #   GlobalPreemptibleAdapter – limits maximum concurrent requests globally.
  #                              paramString = "permit=N" (N = max concurrent calls)
  #   QpsRateLimiterAdapter    – limits average QPS across all callers.
  #                              paramString = "qps=N" (N may be a decimal)
  #   IPQPSRateLimiterAdapter  – limits average QPS per source IP.
  #                              paramString = "qps=N" (N may be a decimal)
  # If no strategy is configured for an endpoint, QpsRateLimiterAdapter with
  # qps=1000 is applied automatically.

  # Per-servlet HTTP rate limits. component is the servlet class simple name.
  http = [
    # {
    #   component = "GetNowBlockServlet",
    #   strategy = "GlobalPreemptibleAdapter",
    #   paramString = "permit=1"
    # },
    # {
    #   component = "GetAccountServlet",
    #   strategy = "IPQPSRateLimiterAdapter",
    #   paramString = "qps=1"
    # },
    # {
    #   component = "ListWitnessesServlet",
    #   strategy = "QpsRateLimiterAdapter",
    #   paramString = "qps=1"
    # }
  ]

  # Per-method gRPC rate limits. component is "package.ServiceName/MethodName".
  rpc = [
    # {
    #   component = "protocol.Wallet/GetBlockByLatestNum2",
    #   strategy = "GlobalPreemptibleAdapter",
    #   paramString = "permit=1"
    # },
    # {
    #   component = "protocol.Wallet/GetAccount",
    #   strategy = "IPQPSRateLimiterAdapter",
    #   paramString = "qps=1"
    # },
    # {
    #   component = "protocol.Wallet/ListWitnesses",
    #   strategy = "QpsRateLimiterAdapter",
    #   paramString = "qps=1"
    # }
  ]

  # P2P message rate limits.
  p2p = {
    # QPS ceiling for individual P2P message types received from peers.
    # Values are doubles; fractional QPS is allowed (e.g. 0.5 = one per 2 s).
    syncBlockChain = 3.0  # SyncBlockChain handshake messages
    fetchInvData = 3.0    # FetchInvData (block/tx fetch) messages
    disconnect = 1.0      # Disconnect messages
  }

  # Node-wide QPS ceiling across all HTTP + gRPC requests combined.
  global.qps = 50000
  # Per-source-IP QPS ceiling across all HTTP + gRPC requests from that IP.
  global.ip.qps = 10000
  # Default per-endpoint QPS limit applied to any endpoint with no explicit strategy.
  global.api.qps = 1000
  # true = reject over-limit requests immediately; false = queue and block the caller.
  apiNonBlocking = false
}
```
