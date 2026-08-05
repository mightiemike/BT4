### Title
Permanent, state-dependent "proof invalid" caching in `ShieldedTransferActuator` can permanently block a valid shielded transaction due to a transient pool-balance condition - (File: `actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java`)

### Summary
`ShieldedTransferActuator.validate()` verifies the zk-SNARK spend/receive proofs once, then caches the pass/fail result permanently in `ZKProofStore`, keyed only by transaction ID, so future re-validations of the same transaction skip real proof re-verification. One of the conditions that can cause a "fail" to be cached, `totalShieldedPoolValue < 0`, depends on mutable, time-varying chain state (`DynamicPropertiesStore.getTotalShieldedPoolValue()`), not on the immutable cryptographic correctness of the proof. Once this transient state check fails at first validation, the transaction is permanently marked invalid and can never be re-validated successfully, even after the pool balance recovers, mirroring the reported VETH bug where a one-shot eligibility gate (merkle proof) is consumed/invalidated based on an unrelated, transient condition (allowance), permanently blocking retry of an otherwise-valid claim.

### Finding Description
In `checkProof`, the very first thing checked is the `ZKProofStore` cache: [1](#0-0) 

If a prior validation attempt for this exact transaction ID recorded `false`, all subsequent validations immediately throw `"record is fail, skip proof"` without ever re-running the actual zk-SNARK proof checks.

The record is written in `validate()`'s catch block whenever `checkProof` throws a `ZkProofValidateException` with `firstValidated == true`: [2](#0-1) 

Crucially, `firstValidated=true` is set not only for cryptographic proof failures (`librustzcashSaplingCheckSpend`/`CheckOutput`/`FinalCheck`), but also for the `totalShieldedPoolValue < 0` check, which reads a **mutable global counter** that changes with every other shielded transaction processed by the network: [3](#0-2) 

Because this check is coupled to the same one-shot cache (`ZKProofStore`, keyed by `tx.getTransactionId()`), a transaction that is fully valid (correct spend/receive proofs, correct binding signature) but happens to be evaluated at a moment when `totalShieldedPoolValue` is temporarily too low (e.g., because other shielded transactions consumed pool capacity first) gets permanently recorded as `false`. If the pool value later recovers (e.g., other users deposit into the shielded pool), the same transaction — if resubmitted/reprocessed with the identical transaction ID (same signed payload) — will be rejected immediately via the cache without ever re-running the pool-balance/proof checks, since `proofStore.has(...)==true` and `proofStore.get(...)==false` short-circuits the entire `checkProof` logic.

This is the java-tron analog of the VETH bug: a one-time-use gate (merkle-proof validity in Vader; zk-proof/pool-balance validity in java-tron) is consumed and permanently marked invalid due to an unrelated, transient external condition (VETH allowance in Vader; global shielded pool value in java-tron), permanently barring the user from a legitimate action that would otherwise succeed once the transient condition resolves.

### Impact Explanation
A user's valid shielded transaction (with a correct, expensive-to-produce zk-SNARK proof) can be permanently blacklisted in `ZKProofStore` due to network-wide pool-balance conditions at the moment of first validation, unrelated to the correctness of the user's own proof. Because the negative cache is keyed by transaction ID and short-circuits all future checks (`"record is fail, skip proof"`), the exact same signed transaction can never succeed again even if resubmitted after the pool balance condition that caused the failure is resolved. This wastes the computational cost of proof generation and can cause user-visible, permanent transaction failure for reasons unrelated to the transaction's own validity — an availability/incorrect-state divergence for the shielded-pool accounting path.

### Likelihood Explanation
This is reachable by any unprivileged user submitting a `ShieldedTransferContract` transaction while the shielded pool is near capacity or heavily utilized by concurrent transactions — a realistic, non-privileged, non-exploit-requiring scenario. No attacker action is needed; it can occur purely from normal network usage / transaction ordering/timing.

### Recommendation
Decouple the permanent proof cache from the mutable pool-balance check:
- Only cache `firstValidated=true`/permanent results for checks that are purely functions of the immutable, signed transaction data (i.e., the actual zk-SNARK cryptographic proof verification), never for checks against mutable global state like `totalShieldedPoolValue`.
- Move the `totalShieldedPoolValue < 0` check outside of `checkProof`/`ZKProofStore` caching entirely (re-evaluate it on every validation, similar to a balance check), so a transaction can succeed on retry once the transient network condition resolves, without needing an entirely new transaction.

### Proof of Concept
1. User A submits a valid `ShieldedTransferContract` transaction (`from_amount`, `to_amount`, correct spend/receive descriptions, correct binding signature).
2. At the moment of `validate()`, `totalShieldedPoolValue` is momentarily low (drained by other concurrent shielded transactions), so `valueBalance` computation makes `totalShieldedPoolValue - valueBalance < 0`, triggering `ZkProofValidateException("shieldedPoolValue error", true)` at [4](#0-3) .
3. `validate()`'s catch block calls `recordProof(tx.getTransactionId(), false)`, permanently marking this exact transaction ID as invalid in `ZKProofStore`.
4. The pool balance later recovers (other users deposit shielded value), which would now make the same transaction's value-balance check pass.
5. User A resubmits the identical signed transaction (same transaction ID). `checkProof` immediately returns via the cache hit path at [5](#0-4) , throwing `"record is fail, skip proof"` without ever re-checking the now-valid pool balance — the transaction can never be processed again despite being otherwise perfectly valid.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L278-285)
```java
    ZKProofStore proofStore = chainBaseManager.getProofStore();
    if (proofStore.has(tx.getTransactionId().getBytes())) {
      if (proofStore.get(tx.getTransactionId().getBytes())) {
        return;
      } else {
        throw new ZkProofValidateException("record is fail, skip proof", false);
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L324-340)
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
```
