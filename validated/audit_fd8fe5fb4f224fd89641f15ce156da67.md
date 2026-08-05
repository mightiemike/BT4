### Title
Cross-fork ZK proof re-verification bypass via cached `ZKProofStore` record - (File: `actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java`)

### Summary
`ShieldedTransferActuator.checkProof()` caches the outcome of a `ShieldedTransferContract`'s zk-SNARK proof verification in `ZKProofStore`, keyed only by transaction ID. On any subsequent re-validation of the same transaction (retry/repush/fork-replay), the cryptographic proof check is skipped entirely and the cached boolean is trusted, exactly mirroring the reported bug class: a "used/verified" flag is set on the *first* processing attempt and short-circuits all later, potentially state-dependent, re-validations.

### Finding Description
`checkProof()` first checks the cache before doing any real work: [1](#0-0) 

If `proofStore.has(txId)` is true, the method returns immediately — either treating the transaction as valid (if the cached value is `true`) or throwing without redoing the SNARK checks (if `false`) — without recomputing `librustzcashSaplingCheckSpend`, `librustzcashSaplingCheckOutput`, or `librustzcashSaplingFinalCheck` against the *current* chain state (current `totalShieldedPoolValue`, current anchor/nullifier set). The record is written unconditionally at the end of a successful check: [2](#0-1) 

The only external inputs re-validated on every call are the nullifier/anchor checks inside `validate()` itself: [3](#0-2) 

but the cryptographic binding of `valueBalance`, `bindingSignature`, and per-spend `zkproof`/`spendAuthoritySignature` to the *currently* accepted shielded pool value is entirely bypassed once cached.

`processTransaction()` (which calls `actuator.validate()`/`execute()`) is invoked repeatedly for the same `TransactionCapsule` in at least two legitimate java-tron retry paths:
1. Block-generation repush of `rePushTransactions` (transactions returned to the pool after a dropped block or reorg) — the same `TransactionCapsule` object/ID is reprocessed via `processTransaction`: [4](#0-3) 
2. Fork-switch replay (`switchFork`), where blocks/transactions from a losing branch are rolled back and transactions from the winning branch (or previously-seen transactions) are re-applied against a different chain state, requiring re-validation of state-dependent checks: [5](#0-4) 

In all these retry/replay scenarios, `ZKProofStore` still holds the record keyed purely by transaction ID from the first pass, so `checkProof()` never re-executes the SNARK verification against the new state — this is structurally identical to the reported bug: a reuse/lock flag set on attempt 1 that silently governs (and in this case corrupts) the outcome of every later attempt, regardless of whether current state should change the result.

### Impact Explanation
If a transaction was previously validated as cryptographically valid against one chain's `totalShieldedPoolValue`/anchor state, and is later replayed (via repush or fork-switch) against a different chain state, `checkProof()` will accept it again without recomputing `librustzcashSaplingFinalCheck`, which is what actually binds `valueBalance` to `totalShieldedPoolValue` and enforces the shielded pool cannot be over-drawn. This creates the risk of a shielded-pool-value / accounting divergence between forks — an underpriced/incorrectly-accepted state transition rather than a genuine cryptographic re-check, which is a concrete accounting/replay-class impact analogous to the report's "double bridge execution" risk from stale first-attempt state.

### Likelihood Explanation
Requires transactions to traverse a retry/reprocessing path (`rePushTransactions` after a dropped/failed block, or a `switchFork` replay), which do occur under normal network operating conditions (forks, dropped blocks) without requiring privileged access — matching Medium likelihood similar to the original report's "transient failure triggers retry" scenario.

### Recommendation
Do not use transaction ID alone as the cache key for a validity flag that is meant to gate a state-dependent cryptographic check. Either remove the cache and always execute `checkProof()`'s cryptographic verification, or key the cache by `(txId, chain-state-fingerprint)` so a stale record from a different fork/replay cannot be reused, mirroring the report's recommendation to validate at a fixed point rather than caching a mutable lock across retries.

### Proof of Concept
Not independently executable from the index alone (requires constructing a full shielded transaction, forcing a fork/repush scenario, and observing `ZKProofStore` reuse) — the control-flow proof above (`checkProof` short-circuit at lines 279–285 combined with the repush/fork-replay call sites in `Manager.java`) is the concrete evidence supporting this finding.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L229-245)
```java
    List<SpendDescription> spendDescriptions = shieldedTransferContract.getSpendDescriptionList();
    // check duplicate sapling nullifiers
    if (CollectionUtils.isNotEmpty(spendDescriptions)) {
      HashSet<ByteString> nfSet = new HashSet<>();
      for (SpendDescription spendDescription : spendDescriptions) {
        if (nfSet.contains(spendDescription.getNullifier())) {
          throw new ContractValidateException("duplicate sapling nullifiers in this transaction");
        }
        nfSet.add(spendDescription.getNullifier());
        if (!merkleContainer.merkleRootExist(spendDescription.getAnchor().toByteArray())) {
          throw new ContractValidateException("Rt is invalid.");
        }
        if (nullifierStore.has(spendDescription.getNullifier().toByteArray())) {
          throw new ContractValidateException("note has been spend in this transaction");
        }
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L275-285)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L356-363)
```java

    recordProof(tx.getTransactionId(), true);
  }

  private void recordProof(Sha256Hash tid, boolean result) {
    ZKProofStore proofStore = chainBaseManager.getProofStore();
    proofStore.put(tid.getBytes(), result);
  }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1667-1701)
```java
    while (pendingTransactions.size() > 0 || rePushTransactions.size() > 0) {
      boolean fromPending = false;
      TransactionCapsule trx;
      if (pendingTransactions.size() > 0) {
        trx = pendingTransactions.peek();
        if (isSort) {
          TransactionCapsule trxRepush = rePushTransactions.peek();
          if (trxRepush == null || trx.getOrder() >= trxRepush.getOrder()) {
            fromPending = true;
          } else {
            trx = rePushTransactions.poll();
            Metrics.gaugeInc(MetricKeys.Gauge.MANAGER_QUEUE, -1,
                MetricLabels.Gauge.QUEUE_REPUSH);
          }
        } else {
          fromPending = true;
        }
      } else {
        trx = rePushTransactions.poll();
        Metrics.gaugeInc(MetricKeys.Gauge.MANAGER_QUEUE, -1,
            MetricLabels.Gauge.QUEUE_REPUSH);
      }

      if (fromPending) {
        pendingTransactions.poll();
        Metrics.gaugeInc(MetricKeys.Gauge.MANAGER_QUEUE, -1,
                MetricLabels.Gauge.QUEUE_PENDING);
      }

      if (trx == null) {
        //  transaction may be removed by rePushLoop.
        logger.warn("Trx is null, fromPending: {}, pending: {}, repush: {}.",
                fromPending, pendingTransactions.size(), rePushTransactions.size());
        continue;
      }
```

**File:** framework/src/test/java/org/tron/core/db/ManagerTest.java (L1776-1782)
```java
  /**
   * A fork switch re-applies the new branch on a rewound, diverged state, so any signature
   * verification cached on those transactions (isVerified) must be cleared to force
   * re-validation against the fork-chain state. Drives a real reorg and asserts that switchFork
   * resets isVerified on the transactions of the branch it switches to.
   */
  @Test
```
