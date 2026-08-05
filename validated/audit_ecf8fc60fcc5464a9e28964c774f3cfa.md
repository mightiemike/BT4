### Title
Cached ZK-Proof Result Keyed by Transaction Hash Bypasses Shielded Pool Invariant Re-Validation - ([File: actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java])

### Summary
`ShieldedTransferActuator` caches the result of an expensive zk-SNARK proof verification in a non-revoking `ZKProofStore`, keyed purely by the transaction hash. On any re-validation of the same transaction (e.g. after a chain reorg returns it to the pending pool), the cache short-circuits *both* the cryptographic proof checks *and* the `totalShieldedPoolValue` conservation check, exactly the "hash used as cardinality / data duplication skips re-validation" pattern flagged in the external report.

### Finding Description
`checkProof()` first looks up the transaction id in `ZKProofStore`; if present and `true`, it returns immediately without re-running any of the checks below it: [1](#0-0) 

Only on a cache miss does the method perform the sapling spend/output/binding-signature verification, compute `valueBalance`, and validate the critical invariant that the running `totalShieldedPoolValue` never goes negative: [2](#0-1) 

The cache entry is written once, at the end of the very same method, keyed only by the transaction id (`tx.getTransactionId()`), with no binding to the merkle anchor, the pool value at the time of verification, or the chain state/fork it was validated against: [3](#0-2) 

Crucially, `ZKProofStore` extends the plain `TronDatabase`, not `TronStoreWithRevoking` used by ordinary chain-state stores: [4](#0-3) 

This mirrors the audit report's root cause almost exactly: a hash (`tx.getTransactionId()`) is used as the sole "cardinality"/primary key for cached validation results, the record is written outside the normal revoking/rollback framework, and a single boolean cached value gates an *entire* branch of the transaction-flow logic (skipping unrelated checks such as the pool-value invariant), rather than the flow performing a single unconditional set of invariant checks every time as the report recommends.

Because `nullifierStore` (the double-spend guard) *is* expected to participate in the revoking/rollback mechanism used elsewhere in the codebase (e.g. `ProposalStore`, `ProposalCreateActuator` pattern shown in `chainbase/src/main/java/org/tron/core/store/ProposalStore.java`), while `ZKProofStore` is not revoking-aware, a chain reorganization can revert the nullifier state (making a previously-spent note spendable again in the new fork) while the `ZKProofStore` cache entry for that transaction id survives untouched. When the same transaction is subsequently re-validated for inclusion in the new fork (this happens naturally via `validate()`/`execute()` being called again on transactions returned to the pending pool), `checkProof()` hits the stale cache and skips the `totalShieldedPoolValue` conservation check for the *new* chain state entirely.

### Impact Explanation
This breaks a core accounting invariant of the shielded pool ("total shielded pool value must never go negative", i.e., transparent-to-shielded and shielded-to-transparent conversions must balance) without re-verifying it against current state after a reorg — an accounting/invariant-divergence impact analogous to the "Data Chain Integrity Can Be Broken" and "Insufficient Validation ... Prevents/Corrupts Finalization" classes in the source report. It is reachable through the normal, unprivileged transaction lifecycle (broadcast → block inclusion → possible fork switch → re-validation), not through any trusted/admin role.

### Likelihood Explanation
Requires a chain reorg (fork switch) that reverts state built with a shielded transaction while retaining the cache, plus resubmission/re-validation of that exact transaction in the new fork. Reorgs are a normal, unprivileged-adjacent occurrence in java-tron's DPoS consensus, so the precondition is not exotic, though exploiting it to actually drive `totalShieldedPoolValue` negative requires the pool state to have diverged between the reverted fork and the new fork — a scenario that is plausible but not guaranteed on every reorg. I was not able to fully trace, within the available tool budget, the exact mempool/reorg re-validation call path that re-invokes `ShieldedTransferActuator.validate()` on previously-included transactions, nor conclusively confirm `NullifierStore`'s exact superclass; these should be verified in a full session.

### Recommendation
Do not use a bare transaction-hash-keyed cache to skip validation logic. Either (a) remove the proof-result cache entirely and always perform the full crypto + invariant checks, or (b) make the cache part of the revoking/rollback framework (`TronStoreWithRevoking`) so it is invalidated consistently with the rest of chain state on reorg, and additionally bind the cached record to the specific chain-state facts it was validated against (e.g., the pool value / anchor) rather than only the transaction id, consistent with the report's recommendation to make all necessary checks unconditional and independent of prior cached data.

### Proof of Concept
Conceptual (not executed, requires live reorg environment):
1. Submit shielded transaction `T` (spend note `N`, valid proof) into block `B1` on fork A. `checkProof()` runs fully, verifies `totalShieldedPoolValue >= 0`, and calls `recordProof(T.txid, true)` into non-revoking `ZKProofStore`.
2. Fork A is abandoned in favor of fork B (reorg); `NullifierStore`/chain state revert via the revoking DB, `T` returns to the pending pool, but `ZKProofStore[T.txid] = true` persists (not revoking-aware).
3. Under fork B, other shielded activity changes `totalShieldedPoolValue` such that including `T` again would actually violate `totalShieldedPoolValue >= 0`.
4. `T` is re-validated for inclusion in fork B: `checkProof()` sees `proofStore.has(T.txid) == true` and returns immediately at [5](#0-4) , skipping the `totalShieldedPoolValue` check block at [6](#0-5) , allowing `T` to execute and corrupt the shielded pool's value-conservation invariant under the new fork's state.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L275-286)
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

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L324-357)
```java
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
      } catch (ZksnarkException e) {
        throw new ZkProofValidateException(e.getMessage(), true);
      } finally {
        JLibrustzcash.librustzcashSaplingVerificationCtxFree(ctx);
      }
    }

    recordProof(tx.getTransactionId(), true);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L360-363)
```java
  private void recordProof(Sha256Hash tid, boolean result) {
    ZKProofStore proofStore = chainBaseManager.getProofStore();
    proofStore.put(tid.getBytes(), result);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/ZKProofStore.java (L1-14)
```java
package org.tron.core.store;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationContext;
import org.springframework.stereotype.Component;
import org.tron.core.db.TronDatabase;

@Component
public class ZKProofStore extends TronDatabase<Boolean> {

  @Autowired
  public ZKProofStore(ApplicationContext ctx) {
    super("zkProof");
  }
```
