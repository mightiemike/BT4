### Title
Zero-cost CPU exhaustion via unmetered zk-SNARK proof verification in `ShieldedTransferActuator.validate()` - (File: `actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java`)

### Summary
`ShieldedTransferActuator.validate()` invokes the CPU-heavy Groth16/Sapling proof verification (`JLibrustzcashSaplingCheckSpend`/`CheckOutput`/`FinalCheck` via `checkProof`) on every syntactically well-formed shielded transaction *before* any fee is actually deducted, and `BandwidthProcessor.consume()` explicitly skips bandwidth billing for `ShieldedTransferContract`. Because the fixed `shieldedTransactionFee` is only debited in `execute()`—which never runs if `validate()` throws—an attacker can force unlimited elliptic-curve pairing checks per broadcast at zero net cost.

### Finding Description
`ShieldedTransferActuator.validate()` unpacks the contract, runs cheap structural checks (`checkSender`, `checkReceiver`, `validateTransparent`) capping spends to 1 and outputs to 2, then calls `checkProof(spendDescriptions, receiveDescriptions, fee)`: [1](#0-0) 

Inside `checkProof`, for every spend/receive description the native, CPU-heavy pairing-based verifications run unconditionally: [2](#0-1) [3](#0-2) 

Crucially, the fee (`calcFee`) is only actually taken from the sender's/blackhole balance inside `execute()`: [4](#0-3) 

`execute()` is only invoked by the transaction pipeline after `validate()` succeeds. If the proof is invalid (e.g., a garbage `Zkproof`/`SpendAuthoritySignature`), `checkProof` throws `ZkProofValidateException` and `validate()` throws before `execute()` ever runs, so **no fee is ever charged** for the transaction that consumed the full pairing-check CPU cost.

Additionally, `BandwidthProcessor.consume()` explicitly bypasses bandwidth billing (the size-based fallback fee/bytes accounting that every other contract type pays) for `ShieldedTransferContract`: [5](#0-4) 

So a shielded transaction with 1 `SpendDescription` + 2 `ReceiveDescription`s (the maximum allowed by `checkSender`/`checkReceiver`) forces up to 3 native pairing checks (`CheckSpend` + 2×`CheckOutput` + `FinalCheck`) per broadcast, entirely for free if the proof is deliberately invalid, since:
- structural pre-checks (`checkSender`/`checkReceiver`/`validateTransparent`) are cheap and easily satisfied with a syntactically valid contract,
- `checkProof` runs before the fee-charging code path in `execute()`,
- `BandwidthProcessor` charges zero bytes-fee for this contract type.

The `ZKProofStore` cache in `checkProof` (lines 278-285) only helps for repeated identical `tx.getTransactionId()`; each new randomized invalid proof produces a distinct transaction ID and bypasses the cache, so an attacker can generate unlimited distinct invalid-but-well-formed transactions.

### Impact Explanation
This is a DoS via the TRON protocol implementation / RPC-API transaction validation pipeline: an unprivileged attacker can flood a public node's `broadcastTransaction` endpoint with well-formed-but-invalid `ShieldedTransferContract`s, forcing expensive native zk-SNARK pairing verification on every one, at effectively zero TRX cost (no bandwidth fee is charged for this contract type, and the shielded fee is never actually debited on validation failure). Sustained flooding can starve the node's transaction validation thread(s)/mempool ingestion, delaying or blocking processing of legitimate transactions (transfers, votes, proposals, unfreezes) submitted in the same window — matching the "DoS via RPC-API" / "DoS via TRON protocol implementation" bounty classes.

### Likelihood Explanation
- Preconditions: public node with shielded transactions enabled (`supportShieldedTransaction`/`ALLOW_SAME_TOKEN_NAME`), which is the default mainnet configuration.
- Attacker capability required: none beyond ability to craft a syntactically valid `ShieldedTransferContract` protobuf with random/garbage cryptographic fields (anchor must reference an existing merkle root, which is a small, discoverable, and non-secret set of values — obtainable via public shielded pool state) and broadcast it via the public RPC. No signing key, no funded account, and no privileged role is needed since transparent-side sender/receiver fields can be omitted, leaving a pure shield-to-shield transaction whose failure mode is caught in `checkProof`, not `validateTransparent`.
- Cost to attacker: effectively $0 per attempt since bandwidth is unbilled and the shielded fee is never actually charged for a validate()-time failure.
- Repeatability: unlimited, since each malformed proof yields a unique transaction ID, defeating the `ZKProofStore` cache.

### Recommendation
- Charge (or reserve/escrow) the `shieldedTransactionFee` (or a proof-verification-specific fee) from the transparent sender balance, or require a minimum bandwidth/energy consumption commitment, before executing the expensive `checkProof` native calls, so that CPU cost is always bound to a paid fee even on validation failure.
- Reinstate standard bandwidth billing (bytes-based) for `ShieldedTransferContract` in `BandwidthProcessor.consume()` rather than unconditionally skipping it.
- Add a per-connection/per-IP or per-account rate limiter specifically for shielded-transaction broadcasts at the RPC layer (e.g., extend `RateLimiterServlet`/`GlobalRateLimiter` to cover `BroadcastServlet`/`broadcastTransaction` gRPC calls with a stricter QPS budget for `ShieldedTransferContract`), independent of the generic HTTP QPS limiter.
- Consider cheap pre-filters (e.g., signature/format sanity checks that can reject a large share of garbage proofs before invoking the native pairing library) ahead of the full `librustzcashSaplingCheckSpend/CheckOutput/FinalCheck` sequence.

### Proof of Concept
```java
// JUnit-style PoC (extends ShieldedTransferActuatorTest fixtures)
@Test
public void unmeteredInvalidProofFloodPoC() throws Exception {
  dbManager.getDynamicPropertiesStore().saveAllowShieldedTransaction(1);

  long start = System.nanoTime();
  int N = 500;
  for (int i = 0; i < N; i++) {
    ZenTransactionBuilder builder = new ZenTransactionBuilder(wallet);
    SpendingKey sk = SpendingKey.random();
    ExpandedSpendingKey expsk = sk.expandedSpendingKey();
    PaymentAddress address = sk.defaultAddress();
    Note note = new Note(address, AMOUNT);
    IncrementalMerkleVoucherContainer voucher = createSimpleMerkleVoucherContainer(note.cm());
    byte[] anchor = voucher.root().getContent().toByteArray();
    dbManager.getMerkleContainer().putMerkleTreeIntoStore(anchor,
        voucher.getVoucherCapsule().getTree());
    builder.addSpend(expsk, note, anchor, voucher);
    // Corrupt output note commitment / add a second garbage receive to hit the 2-receive cap
    addZeroValueOutputNote(builder); // deliberately mismatched -> proof fails FinalCheck/CheckSpend

    TransactionCapsule transactionCap = builder.build();
    Contract contract = transactionCap.getInstance().toBuilder()
        .getRawDataBuilder().getContract(0);
    ShieldedTransferActuator actuator = new ShieldedTransferActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setContract(contract).setTx(transactionCap);

    try {
      actuator.validate(); // full native pairing checks run here
    } catch (ContractValidateException expected) {
      // e.g. "librustzcashSaplingCheckSpend error" / "librustzcashSaplingFinalCheck error"
    }
    // No fee ever debited: execute() never called, BandwidthProcessor skips this contract type.
  }
  long elapsedMs = (System.nanoTime() - start) / 1_000_000;

  // Assert: total attacker-paid fee across N invalid txs is 0
  // while elapsedMs scales linearly with N * (1 CheckSpend + 2 CheckOutput + 1 FinalCheck),
  // demonstrating unbounded CPU cost with zero economic cost.
  System.out.println("Elapsed ms for " + N + " invalid shielded validations: " + elapsedMs);
}
```
Expected result: `validate()` throws `ContractValidateException` for every iteration (proof fails), no balance/blackhole fee is ever deducted, yet wall-clock time grows linearly with N, confirming CPU cost is unmetered/unbounded relative to attacker-paid fee (which is $0).

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L73-87)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L262-273)
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

    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L291-322)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L342-349)
```java
        if (!JLibrustzcash.librustzcashSaplingFinalCheck(
            new FinalCheckParams(ctx,
                valueBalance,
                shieldedTransferContract.getBindingSignature().toByteArray(),
                signHash)
        )) {
          throw new ZkProofValidateException("librustzcashSaplingFinalCheck error", true);
        }
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L122-125)
```java
    for (Contract contract : contracts) {
      if (contract.getType() == ShieldedTransferContract) {
        continue;
      }
```
