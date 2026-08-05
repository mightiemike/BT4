### Title
Unrateified zk-SNARK proof verification cost lets attackers cheaply trigger expensive CPU work before rejection - ([File: framework/src/main/java/org/tron/core/db/Manager.java])

### Finding Description
`Wallet.broadcastTransaction` [1](#0-0)  calls `dbManager.pushTransaction(trx)`, which in `Manager.pushTransaction` checks `shieldedTransInPendingCounts.get() >= shieldedTransInPendingMaxCounts` **before** calling `processTransaction`, and only increments the counter **after** `processTransaction` succeeds [2](#0-1) . `processTransaction` invokes the actuator's `validate()`, and for `ShieldedTransferContract` this is `ShieldedTransferActuator.validate()` → `checkProof()`, which performs the actual zk-SNARK verification (`librustzcashSaplingCheckSpend`, `librustzcashSaplingCheckOutput`, `librustzcashSaplingFinalCheck`) [3](#0-2) . These are computationally expensive elliptic-curve pairing operations, as evidenced by the dedicated benchmark tests measuring their wall-clock cost [4](#0-3) .

Because the pending-slot budget check happens before the expensive verification step, and the counter is not incremented (and no fee is charged/settled) when verification fails, a submission with a well-formed-but-invalid proof (bad nullifier, reused/duplicate nullifier, tampered zk-proof) does **not** consume the `shieldedTransInPendingMaxCounts` slot budget, yet it still forces the node to run the full zk-SNARK verification pipeline before failing. This means the `shieldedTransInPendingMaxCounts` limit protects the *pending slot* resource but does nothing to rate-limit or price the *CPU cost* of proof verification itself.

Existing guards that partially mitigate but do not eliminate this:
- `trx.validateSignature(...)` runs before the shielded-slot check [5](#0-4) , requiring a validly signed transaction, but this is cheap relative to zk-SNARK pairing checks and does not require the sender to have any TRX/energy/bandwidth balance for shield-to-shield transactions without a transparent input (the shielded contract fee is only charged in `execute()`, which runs after `validate()`/`checkProof()` succeeds — a failing proof means no cost is ever settled).
- General queue guards (`dbManager.isTooManyPending()`, `trxCacheEnable` dup cache by transaction ID) throttle overall pending queue size and exact duplicate transactions, but not distinct crafted transactions with different nullifiers/proofs each requiring fresh verification.
- The proof cache (`ZKProofStore`) in `checkProof()` only helps for re-submissions of the *exact same* transaction ID; an attacker varying nullifiers/proof bytes always produces a new transaction ID and bypasses this cache.

### Impact Explanation
An attacker can submit a stream of well-formed `ShieldedTransferContract` transactions with syntactically valid-length zk-proof fields but semantically invalid/duplicate nullifiers or invalid proof bytes, each forcing the node to execute full elliptic-curve pairing verification in `checkProof()` before rejecting the transaction with a `ContractValidateException`. Because the `shieldedTransInPendingMaxCounts` slot is not consumed on failure, this does not even hit that specific 10-slot cap, so the attack is bounded only by generic network/queue rate limiters (e.g., `netMaxTrxPerSecond`, `maxTransactionPendingSize`), not by the actual zk-proof CPU cost. This can degrade node responsiveness/consensus performance for legitimate shielded transaction users and other transaction processing, since expensive verification work is performed essentially for free (no fee is settled on failure).

### Likelihood Explanation
Feasible for any unprivileged attacker with the ability to reach `/wallet/broadcasttransaction` and craft `ShieldedTransferContract` payloads with correct field lengths (32/64/192-byte fields as validated by `LibrustzcashParam`) but wrong content. Building such payloads requires only knowledge of the protobuf structure and byte-length constants (all public/open-source), not real spending keys or valid witnesses. Repeatable indefinitely as long as the attacker can produce distinct transaction IDs (e.g., by varying nonce/expiration/timestamp fields) to bypass both the transaction-id dedup cache and the `ZKProofStore` cache.

### Recommendation
- Add a lightweight, cheap pre-check (proportional cost) before running the full zk-SNARK verification — e.g., rate-limit shielded transaction submissions per source/IP/account independently of the `shieldedTransInPendingMaxCounts` pending-slot metric, or require the pending-slot reservation to be taken *before* `processTransaction` runs (and only released, not incremented-after) so that a fixed number of expensive verifications can be in flight regardless of outcome.
- Consider charging a minimal upfront bandwidth/energy cost for shielded transaction submission attempts (independent of proof success) to make CPU-expensive proof verification "priced" work rather than free.
- Cache negative outcomes (invalid nullifier/proof) per nullifier or account and apply an increasing backoff for repeated invalid submissions from the same signer to blunt cheap-signature-driven flooding.

### Proof of Concept
```java
// Integration test sketch (framework/src/test/java/org/tron/core/db/ManagerShieldedDosTest.java)
@Test
public void testShieldedInvalidProofFloodBypassesPendingSlotBudget() throws Exception {
  dbManager.getDynamicPropertiesStore().saveAllowShieldedTransaction(1);
  dbManager.getDynamicPropertiesStore().saveTotalShieldedPoolValue(100 * 1000000L);

  int maxCounts = Args.getInstance().getShieldedTransInPendingMaxCounts(); // e.g. 10
  int floodCount = maxCounts * 20; // far exceed the slot budget

  long totalNanos = 0;
  int rejectedCount = 0;

  for (int i = 0; i < floodCount; i++) {
    // Build a syntactically valid ShieldedTransferContract with a
    // random/duplicate 32-byte nullifier and a garbage-but-correctly-sized
    // 192-byte zkproof / 64-byte spendAuthSig, distinct raw_data (e.g. timestamp)
    // to yield a unique transaction id each iteration.
    TransactionCapsule trx = buildInvalidShieldedTransaction(i);

    long start = System.nanoTime();
    try {
      dbManager.pushTransaction(trx);
      Assert.fail("expected ContractValidateException due to invalid proof/nullifier");
    } catch (ContractValidateException e) {
      rejectedCount++;
      // Expect zk-SNARK verification error messages such as
      // "librustzcashSaplingCheckSpend error" / "note has been spend in this transaction"
    }
    totalNanos += System.nanoTime() - start;

    // Assert the pending shielded-slot counter never grows despite floodCount >> maxCounts,
    // proving the failed verifications bypass the intended budget.
    Assert.assertTrue(dbManager.getShieldedTransInPendingCounts().get() <= maxCounts);
  }

  double avgMillisPerRejectedTx = (totalNanos / 1_000_000.0) / rejectedCount;
  // Assert the observed CPU cost per rejected transaction is non-trivial
  // (zk-SNARK pairing checks typically cost multiple milliseconds),
  // while zero fee was ever charged/settled for any of these transactions.
  System.out.println("Avg CPU ms per rejected shielded tx: " + avgMillisPerRejectedTx);
  Assert.assertTrue("zk verification cost should be non-negligible",
      avgMillisPerRejectedTx > 1.0);
}
```
Expected result: all `floodCount` submissions are rejected with a `ContractValidateException` originating from `ShieldedTransferActuator.checkProof()` (confirming full zk-SNARK verification ran each time), the `shieldedTransInPendingCounts` gauge never exceeds `shieldedTransInPendingMaxCounts` (confirming the slot budget is not the limiting factor), and the average per-rejected-transaction CPU time is measurably non-trivial — demonstrating uncompensated CPU work extractable at will by an unprivileged submitter.

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L574-576)
```java
      trx.checkExpiration(chainBaseManager.getNextBlockSlotTime());
      dbManager.pushTransaction(trx);
      TransactionMessage message = new TransactionMessage(trx.getInstance().toByteArray());
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L904-909)
```java
    try {
      if (!trx.validateSignature(chainBaseManager.getAccountStore(),
          chainBaseManager.getDynamicPropertiesStore())) {
        throw new ValidateSignatureException(String.format("trans sig validate failed, id: %s",
            trx.getTransactionId()));
      }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L924-944)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L275-322)
```java
  private void checkProof(List<SpendDescription> spendDescriptions,
      List<ReceiveDescription> receiveDescriptions, long fee) throws ZkProofValidateException {
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
```

**File:** framework/src/test/java/org/tron/core/zksnark/ShieldedReceiveTest.java (L446-464)
```java
  private long benchmarkVerifySpend(String spend, String dataToBeSigned) throws ZksnarkException {
    long startTime = System.currentTimeMillis();
    long ctx = JLibrustzcash.librustzcashSaplingProvingCtxInit();

    CheckSpendParams checkSpendParams = CheckSpendParams.decode(ctx,
        ByteArray.fromHexString(spend),
        ByteArray.fromHexString(dataToBeSigned));

    boolean ok = JLibrustzcash.librustzcashSaplingCheckSpend(checkSpendParams);

    JLibrustzcash.librustzcashSaplingProvingCtxFree(ctx);
    Assert.assertTrue(ok);

    long endTime = System.currentTimeMillis();
    long time = endTime - startTime;

    System.out.println("--- time is: " + time + ", result is " + ok);
    return time;
  }
```
