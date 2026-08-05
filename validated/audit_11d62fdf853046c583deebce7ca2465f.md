### Title
Zero-cost CPU exhaustion via serialized shielded transaction proof verification before signature failure - ([File: actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java])

### Summary
`ShieldedTransferActuator.validate()` runs the full native Sapling pairing-verification sequence (`librustzcashSaplingCheckSpend`, `librustzcashSaplingCheckOutput`, `librustzcashSaplingFinalCheck`) inside `checkProof()` *before* it can determine that the `spendAuthoritySignature`/`bindingSignature` is invalid, and this happens for every "fresh" (never-cached) `ShieldedTransferContract`. Because `Manager.pushTransaction()` serializes all mempool validation behind a single lock and rejects the transaction with zero fee charged, an attacker can flood the network with syntactically-valid, garbage-signed `ShieldedTransferContract` transactions to consume disproportionate CPU on every full node at effectively no cost.

### Finding Description
`ShieldedTransferActuator.validate()` unpacks the contract, does cheap structural checks (`checkSender`, `checkReceiver`, nullifier/anchor/duplicate checks), and only then calls `checkProof()`: [1](#0-0) 

`checkProof()` first checks a `ZKProofStore` cache keyed by transaction id, but a fresh, never-seen transaction always misses this cache, so the code proceeds to run the full pairing-based verification sequence: [2](#0-1) 

The `librustzcashSaplingCheckSpend` / `librustzcashSaplingCheckOutput` calls perform elliptic-curve pairing verification of both the zk-proof and the signature material together in native code; there is no cheap, separate signature-only pre-check in the Java layer before these expensive calls execute: [3](#0-2) 

Only after these expensive native calls run does `librustzcashSaplingFinalCheck` (also expensive) fail on an invalid `bindingSignature`, and only then is `ZkProofValidateException` thrown, causing `validate()` to fail: [4](#0-3) 

Test cases in the repo confirm that transactions with mismatched fee/value (equivalent to a broken binding signature) reliably fail at `librustzcashSaplingFinalCheck`, i.e., only after the expensive checks run: [5](#0-4) 

Reachability and lack of cost-scaling:
- Because `validate()` throws before `execute()` is ever invoked, **no fee is deducted** for a failing transaction — fee assignment (`ret.setShieldedTransactionFee(fee)`) only happens inside `execute()`, which is unreachable for invalid-signature transactions. [6](#0-5) 
- `Manager.pushTransaction()` performs the expensive `processTransaction()` (which calls `validate()`/`checkProof()`) inside a globally serialized `synchronized (this)` block, meaning every invalid-signature shielded transaction still consumes one full slot of the node's single serialized validation path before being rejected: [7](#0-6) 
- The only shielded-transaction-specific throttle, `shieldedTransInPendingCounts >= shieldedTransInPendingMaxCounts`, is checked *before* `processTransaction()` runs, but the counter is only incremented **after** a transaction succeeds and is added to `pendingTransactions`. Failing transactions (our attack case) never increment this counter, so this guard does not bound the number of expensive-but-failing verifications an attacker can trigger: [8](#0-7) 
- At the P2P layer, `TransactionsMsgHandler.check()` only validates signature *byte length*, not correctness, so garbage-but-correctly-sized signatures pass this cheap gate and reach `pushTransaction`: [9](#0-8) 
- Failures from `ContractValidateException`/`ZkProofValidateException` are wrapped by `TronNetDelegate.pushTransaction` as `P2pException.TypeEnum.TRX_EXE_FAILED`, not `BAD_TRX`: [10](#0-9) 
  and `handleTransaction` only bans/disconnects a peer on `TypeEnum.BAD_TRX`, not `TRX_EXE_FAILED`: [11](#0-10) 
  meaning a single malicious peer can keep resubmitting fresh invalid-signature shielded transactions without ever being disconnected.

Attacker inputs required are cheap: each transaction just needs a fresh (unique) nullifier/commitment/`Zkproof` byte blob of the correct fixed size (the native pairing-verification routines execute the same fixed amount of elliptic-curve work regardless of whether the underlying proof/signature data is cryptographically valid or random garbage of correct length), plus a random `spendAuthoritySignature`/`bindingSignature`.

### Impact Explanation
An attacker can broadcast a stream of distinct `ShieldedTransferContract` transactions, each forcing the honest node's single serialized transaction-processing path (`Manager.pushTransaction`'s `synchronized(this)` block) to execute the full Sapling pairing verification sequence before rejecting the transaction — at zero fee cost (transactions are rejected in `validate()`, before any fee is charged) and without risking peer disconnection (failures classified as `TRX_EXE_FAILED`, not `BAD_TRX`). Because transaction processing (including for non-shielded transactions) is serialized behind the same lock, this can degrade mempool-wide transaction throughput and block-production timing on every full node that receives the flood.

### Likelihood Explanation
- No privileged access is required; only `dynamicStore.supportShieldedTransaction()` must be enabled by governance (a normal, expected network state, not an attacker precondition to defeat).
- The attacker only needs to construct syntactically valid protobuf structures with correctly-sized byte fields (readily automatable, no real proving-key computation needed since the native pairing check cost is independent of whether the proof data is genuine).
- The existing shielded-transaction-specific pending-count throttle does not apply to failing transactions, and generic P2P bad-peer banning does not trigger for this failure class, so the attack is repeatable without escalating cost or being blocked by existing peer-level defenses that I could verify in the repo.
- Practical amplification is somewhat limited by fixed caps on descriptions per transaction (`checkSender`/`checkReceiver` limit to at most 1 spend + 2 receives), and by the single global lock (verification is serialized, not parallelized, which bounds worst-case CPU to node's serialized-processing capacity) — but this still means an attacker can occupy the node's entire transaction-validation critical path with near-zero-cost transactions.

### Recommendation
- Add a lightweight, cheap pre-check (e.g., basic structural/format sanity or a separate cheap signature malleability check outside the native pairing routines) before invoking `librustzcashSaplingCheckSpend`/`CheckOutput`/`FinalCheck`, if the underlying native library exposes any way to validate signature encoding cheaply prior to the pairing check.
- Track and penalize peers that repeatedly submit shielded transactions failing `checkProof()`/signature checks (treat repeated `ZkProofValidateException`/signature failures similarly to `BAD_TRX` for banning purposes), not just structural `BAD_TRX` violations.
- Introduce a per-peer or global rate limit specifically on the number of *fresh* (uncached) shielded proof verifications performed per unit time, independent of the `shieldedTransInPendingCounts` success-only counter.
- Consider requiring a minimum bandwidth/energy stake or fee to be reserved/burned even for transactions that fail signature validation, to remove the "effectively free" submission cost.

### Proof of Concept
Java unit test (extending `ShieldedTransferActuatorTest`) demonstrating that `checkProof()`'s expensive native calls execute prior to rejection, and that repeated calls are not otherwise cost-mitigated:

```java
@Test
public void invalidSignatureCostsFullProofVerification() throws Exception {
  dbManager.getDynamicPropertiesStore().saveAllowShieldedTransaction(1);
  dbManager.getDynamicPropertiesStore().saveTotalShieldedPoolValue(AMOUNT * 10);

  long totalTime = 0;
  int N = 20;
  for (int i = 0; i < N; i++) {
    // build a fresh, syntactically valid ShieldedTransferContract with a genuine spend/output
    // (unique nullifier/commitment each iteration), then corrupt spendAuthoritySignature/
    // bindingSignature with random bytes of correct length before building the final tx.
    TransactionCapsule transactionCap = buildValidShieldedTxWithCorruptedSignature(i);

    Contract contract = transactionCap.getInstance().toBuilder()
        .getRawDataBuilder().getContract(0);
    ShieldedTransferActuator actuator = new ShieldedTransferActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setContract(contract).setTx(transactionCap);

    long start = System.nanoTime();
    try {
      actuator.validate();
      Assert.fail("expected validation failure due to invalid signature");
    } catch (ContractValidateException e) {
      // Expected: rejected only AFTER librustzcashSaplingCheckSpend/CheckOutput/FinalCheck ran.
      Assert.assertTrue(e.getMessage().contains("librustzcashSapling"));
    }
    totalTime += (System.nanoTime() - start);
  }

  // Assert the CPU time is materially large (proportional to N expensive native verifications),
  // while the attacker paid zero fee (execute() was never reached, so no
  // ret.setShieldedTransactionFee(fee) occurred, and no ContractExeException/fee-deduction path
  // is exercised).
  Assert.assertTrue("expected material CPU cost from repeated pairing verification",
      totalTime > 0);
}
```

Expected assertion outcomes:
1. Each iteration throws `ContractValidateException` with a message originating from `librustzcashSaplingCheckSpend`, `librustzcashSaplingCheckOutput`, or `librustzcashSaplingFinalCheck` — confirming the expensive native calls ran to completion before rejection [12](#0-11) .
2. No fee is ever recorded for these transactions since `execute()` (where `ret.setShieldedTransactionFee(fee)` occurs) is never called [13](#0-12) .
3. (Integration-level extension) Push N such transactions through `Manager.pushTransaction`/`TransactionsMsgHandler.handleTransaction` from a single simulated peer and confirm the peer is never disconnected (`peer.isBadPeer()` remains false), since the failure is classified as `TRX_EXE_FAILED` rather than `BAD_TRX` [11](#0-10) .

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L56-112)
```java
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
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L262-270)
```java
    //check spendProofs receiveProofs and Binding sign hash
    try {
      checkProof(spendDescriptions, receiveDescriptions, fee);
    } catch (ZkProofValidateException e) {
      if (e.isFirstValidated()) {
        recordProof(tx.getTransactionId(), false);
      }
      throw e;
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L277-349)
```java
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    ZKProofStore proofStore = chainBaseManager.getProofStore();
    if (proofStore.has(tx.getTransactionId().getBytes())) {
      if (proofStore.get(tx.getTransactionId().getBytes())) {
        return;
      } else {
        throw new ZkProofValidateException("record is fail, skip proof", false);
      }
    }

    byte[] signHash = getShieldTransactionHashIgnoreTypeException(tx.getInstance());

    if (CollectionUtils.isNotEmpty(spendDescriptions)
        || CollectionUtils.isNotEmpty(receiveDescriptions)) {
      long ctx = JLibrustzcash.librustzcashSaplingVerificationCtxInit();
      try {
        for (SpendDescription spendDescription : spendDescriptions) {
          if (!JLibrustzcash.librustzcashSaplingCheckSpend(
              new CheckSpendParams(ctx,
                  spendDescription.getValueCommitment().toByteArray(),
                  spendDescription.getAnchor().toByteArray(),
                  spendDescription.getNullifier().toByteArray(),
                  spendDescription.getRk().toByteArray(),
                  spendDescription.getZkproof().toByteArray(),
                  spendDescription.getSpendAuthoritySignature().toByteArray(),
                  signHash)
          )) {
            throw new ZkProofValidateException("librustzcashSaplingCheckSpend error", true);
          }
        }

        for (ReceiveDescription receiveDescription : receiveDescriptions) {
          if (receiveDescription.getCEnc().size() != ZC_ENCCIPHERTEXT_SIZE
              || receiveDescription.getCOut().size() != ZC_OUTCIPHERTEXT_SIZE) {
            throw new ZkProofValidateException("Cout or CEnc size error", true);
          }
          if (!JLibrustzcash.librustzcashSaplingCheckOutput(
              new CheckOutputParams(ctx,
                  receiveDescription.getValueCommitment().toByteArray(),
                  receiveDescription.getNoteCommitment().toByteArray(),
                  receiveDescription.getEpk().toByteArray(),
                  receiveDescription.getZkproof().toByteArray())
          )) {
            throw new ZkProofValidateException("librustzcashSaplingCheckOutput error", true);
          }
        }

        long valueBalance;
        long totalShieldedPoolValue = dynamicStore
            .getTotalShieldedPoolValue();
        try {
          valueBalance = addExact(subtractExact(
              shieldedTransferContract.getToAmount(),
              shieldedTransferContract.getFromAmount()), fee);
          totalShieldedPoolValue = subtractExact(
              totalShieldedPoolValue, valueBalance);
        } catch (ArithmeticException e) {
          logger.debug(e.getMessage(), e);
          throw new ZkProofValidateException(e.getMessage(), true);
        }

        if (totalShieldedPoolValue < 0) {
          throw new ZkProofValidateException("shieldedPoolValue error", true);
        }

        if (!JLibrustzcash.librustzcashSaplingFinalCheck(
            new FinalCheckParams(ctx,
                valueBalance,
                shieldedTransferContract.getBindingSignature().toByteArray(),
                signHash)
        )) {
          throw new ZkProofValidateException("librustzcashSaplingFinalCheck error", true);
        }
```

**File:** chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java (L166-180)
```java
  public static boolean librustzcashSaplingCheckSpend(CheckSpendParams params) {
    return INSTANCE.librustzcashSaplingCheckSpend(params.getCtx(), params.getCv(),
        params.getAnchor(), params.getNullifier(), params.getRk(), params.getZkproof(),
        params.getSpendAuthSig(), params.getSighashValue());
  }

  public static boolean librustzcashSaplingCheckOutput(CheckOutputParams params) {
    return INSTANCE.librustzcashSaplingCheckOutput(params.getCtx(), params.getCv(),
        params.getCm(), params.getEphemeralKey(), params.getZkproof());
  }

  public static boolean librustzcashSaplingFinalCheck(FinalCheckParams params) {
    return INSTANCE.librustzcashSaplingFinalCheck(params.getCtx(),
        params.getValueBalance(), params.getBindingSig(), params.getSighashValue());
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/ShieldedTransferActuatorTest.java (L304-312)
```java
      actuator.validate();
      actuator.execute(ret);
      Assert.assertTrue(false);
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("librustzcashSaplingFinalCheck error", e.getMessage());
    } catch (Exception e) {
      Assert.assertTrue(false);
    }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L911-944)
```java
      synchronized (transactionLock) {
        while (true) {
          try {
            if (isBlockWaitingLock()) {
              TimeUnit.MILLISECONDS.sleep(SLEEP_FOR_WAIT_LOCK);
            } else {
              break;
            }
          } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            logger.debug("The wait has been interrupted.");
          }
        }
        synchronized (this) {
          if (isShieldedTransaction(trx.getInstance())
                  && shieldedTransInPendingCounts.get() >= shieldedTransInPendingMaxCounts) {
            return false;
          }
          if (!session.valid()) {
            session.setValue(revokingStore.buildSession());
          }

          try (ISession tmpSession = revokingStore.buildSession()) {
            processTransaction(trx, null);
            trx.setTrxTrace(null);
            pendingTransactions.add(trx);
            Metrics.gaugeInc(MetricKeys.Gauge.MANAGER_QUEUE, 1,
                    MetricLabels.Gauge.QUEUE_PENDING);
            tmpSession.merge();
          }
          if (isShieldedTransaction(trx.getInstance())) {
            shieldedTransInPendingCounts.incrementAndGet();
          }
        }
```

**File:** framework/src/main/java/org/tron/core/net/messagehandler/TransactionsMsgHandler.java (L129-153)
```java
  private void check(PeerConnection peer, TransactionsMessage msg) throws P2pException {
    List<Transaction> list = msg.getTransactions().getTransactionsList();
    Set<Sha256Hash> seen = new HashSet<>(list.size() * 2);
    for (Transaction trx : list) {
      Sha256Hash id = new TransactionMessage(trx).getMessageId();
      if (!seen.add(id)) {
        throw new P2pException(TypeEnum.BAD_MESSAGE,
            "TransactionsMessage contains duplicate transaction: " + id);
      }
      Item item = new Item(id, InventoryType.TRX);
      if (!peer.getAdvInvRequest().containsKey(item)) {
        throw new P2pException(TypeEnum.BAD_MESSAGE,
            "trx: " + msg.getMessageId() + " without request.");
      }
      if (trx.getRawData().getContractCount() < 1) {
        throw new P2pException(TypeEnum.BAD_TRX,
            "tx " + item.getHash() + " contract size should be greater than 0");
      }
      for (ByteString sig : trx.getSignatureList()) {
        if (!SignUtils.isValidLength(sig.size())) {
          throw new P2pException(TypeEnum.BAD_TRX,
              "tx " + item.getHash() + " signature size is " + sig.size());
        }
      }
    }
```

**File:** framework/src/main/java/org/tron/core/net/messagehandler/TransactionsMsgHandler.java (L184-201)
```java
    try {
      trx.getTransactionCapsule().checkExpiration(chainBaseManager.getNextBlockSlotTime());
      tronNetDelegate.pushTransaction(trx.getTransactionCapsule());
      advService.broadcast(trx);
    } catch (P2pException e) {
      logger.warn("Trx {} from peer {} process failed. type: {}, reason: {}",
          trx.getMessageId(), peer.getInetAddress(), e.getType(), e.getMessage());
      if (e.getType().equals(TypeEnum.BAD_TRX)) {
        peer.setBadPeer(true);
        peer.disconnect(ReasonCode.BAD_TX);
      }
    } catch (TransactionExpirationException e) {
      logger.warn("{}. trx: {}, peer: {}",
          e.getMessage(), trx.getMessageId(), peer.getInetAddress());
    } catch (Exception e) {
      logger.error("Trx {} from peer {} process failed", trx.getMessageId(), peer.getInetAddress(),
          e);
    }
```

**File:** framework/src/main/java/org/tron/core/net/TronNetDelegate.java (L325-344)
```java
  public void pushTransaction(TransactionCapsule trx) throws P2pException {
    try {
      trx.setTime(System.currentTimeMillis());
      dbManager.pushTransaction(trx);
    } catch (ContractSizeNotEqualToOneException
        | VMIllegalException e) {
      throw new P2pException(TypeEnum.BAD_TRX, e);
    } catch (ContractValidateException
        | ValidateSignatureException
        | ContractExeException
        | DupTransactionException
        | TaposException
        | TooBigTransactionException
        | TransactionExpirationException
        | ReceiptCheckErrException
        | TooBigTransactionResultException
        | AccountResourceInsufficientException e) {
      throw new P2pException(TypeEnum.TRX_EXE_FAILED, e);
    }
  }
```
