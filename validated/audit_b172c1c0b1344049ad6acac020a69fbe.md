### Title
Witness/voter rewards accrued when a witness has zero total votes are permanently lost with no recovery mechanism - (File: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java`)

### Summary
This is a valid analog of the reported bug class. In the Curve gauge, `last_update` (the checkpoint) advances even while `totalSupply == 0`, so rewards deposited during a zero-supply window are never credited to any share and become permanently stuck. Java-tron has a structurally identical pattern in its DPoS voter-reward ("VI") mechanism: block rewards, standby-witness rewards, and transaction-fee rewards are credited to a witness's per-cycle reward bucket unconditionally, while the checkpoint that would distribute that bucket to voters (`accumulateWitnessVi`) simply skips accumulation and forwards the previous checkpoint whenever the witness's snapshot vote count for that cycle is zero — silently stranding the reward with no path to reclaim or redistribute it.

### Finding Description
`MortgageService.payReward()` unconditionally records reward value into the witness's per-cycle bucket via `delegationStore.addReward(cycle, witnessAddress, value)`, regardless of whether any voter is currently backing that witness for the cycle: [1](#0-0) 

This is invoked from block-reward, standby-witness-reward, and transaction-fee-reward payout paths: [2](#0-1) 

Critically, `payTransactionFeeReward` is funded by decrementing the real, previously-collected `transactionFeePool`, so the value earmarked into the witness's per-cycle reward bucket represents already-accounted, real value being subtracted from a shared pool: [3](#0-2) 

The only mechanism that turns a witness's per-cycle reward bucket into a claimable voter credit is `accumulateWitnessVi` (analogous to the gauge's reward-per-share checkpoint). When the witness's snapshotted vote count for that cycle is zero, the delta is not computed at all — the previous VI is just carried forward, and the reward recorded via `addReward` for that cycle is never converted into any voter's claimable VI increment: [4](#0-3) 

The same logic is duplicated in the merkle-checkpoint recomputation path used for the legacy reward algorithm: [5](#0-4) 

The per-cycle vote-count snapshot consumed by `accumulateWitnessVi` (i.e., the "totalSupply" analog) is written once per maintenance cycle from the witness's current vote count, and is not re-derived from votes cast within the cycle: [6](#0-5) 

If all voters for an active witness unvote (drop votes to zero) after this snapshot is taken but before the next maintenance cycle, the witness can still be scheduled and can still receive block rewards / transaction-fee rewards / standby rewards during the remainder of the cycle (payment is tied to being the scheduled block producer, not to instantaneous vote count). Those reward buckets are written via `addReward`, but at the next maintenance boundary `accumulateWitnessVi` sees `voteCount == 0` for that cycle and simply forwards the prior VI — the reward that was subtracted from the (in the fee-pool case, real) pool is never folded into any voter's claimable VI and is never retried, refunded, or reclaimed in any later cycle. There is no code path anywhere in `DelegationStore`, `MortgageService`, or `RewardViCalService` that revisits a stranded per-cycle `reward` bucket once its accumulation cycle has been skipped due to zero vote count.

### Impact Explanation
This constitutes a real, unrecoverable accounting loss: value already debited from the shared `transactionFeePool` (a real pool funded by user-paid transaction fees) is earmarked to a witness/cycle bucket that can never be converted into a voter credit, and is not returned to the pool, to the witness, or to any account. This matches the report's "HIGH severity — rewards blocked with no possibility to retrieve them" characterization, since the stranded value is simply erased from the system's accounting with no path to recovery for the fee-pool contributors, the witness's voters, or the protocol.

### Likelihood Explanation
The zero-vote-count-for-an-active-witness condition is an edge case (requires most/all backers of a currently scheduled witness to unvote within the same cycle window), so it is not triggerable at will by an attacker, but it is a legitimate reachable state given permissionless unfreeze/unvote operations available to any unprivileged user, combined with the fixed-per-cycle vote snapshot used by the reward checkpoint. No privileged role is required to induce it.

### Recommendation
Guard the reward-accumulation checkpoint so that per-cycle reward buckets computed while `voteCount == 0` are not silently discarded: either (a) do not let `payReward`/`addReward` earmark real pool funds (transaction fee pool) to a witness/cycle combination with zero votes and instead redirect/retain that value in the pool for later cycles, or (b) make `accumulateWitnessVi` (and its duplicate in `RewardViCalService`) roll forward any unattributed per-cycle `reward` value into a subsequent cycle where `voteCount > 0` rather than dropping it, analogous to updating `last_update`/checkpoint math only when the distribution denominator is non-zero.

### Proof of Concept
1. Witness `W` is elected into the active set with a nontrivial vote total at cycle `N` maintenance boundary; `delegationStore.setWitnessVote(N, W, voteCountSnapshot)` is written (`MaintenanceManager.doMaintenance`, `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java:154-162`).
2. During cycle `N`, all accounts that voted for `W` call unfreeze/re-vote operations, driving `W`'s live vote count to `0`, while `W` remains scheduled to produce blocks for the rest of cycle `N` (active-set membership only updates at the next maintenance boundary).
3. `W` produces one or more blocks in cycle `N`; `Manager.payReward` calls `mortgageService.payBlockReward(...)` and, if `supportTransactionFeePool()` is enabled, `mortgageService.payTransactionFeeReward(...)`, decrementing the real `transactionFeePool` and calling `delegationStore.addReward(N, W, value)` (`chainbase/src/main/java/org/tron/core/service/MortgageService.java:69-87`, `framework/src/main/java/org/tron/core/db/Manager.java:1946-1985`).
4. At the maintenance boundary for cycle `N`→`N+1`, `delegationStore.accumulateWitnessVi(N, W, 0)` is invoked with the zero snapshot from step 1; since `voteCount == 0`, the delta-VI branch is skipped and the previous VI is forwarded unchanged (`chainbase/src/main/java/org/tron/core/store/DelegationStore.java:133-146`).
5. The `reward` value recorded for `(N, W)` in step 3 is never converted into any voter's VI increment in any later cycle, and the amount already subtracted from `transactionFeePool` is permanently unaccounted for and unrecoverable by any party.

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L53-77)
```java
  public void payStandbyWitness() {
    List<WitnessCapsule> witnessStandbys = witnessStore.getWitnessStandby(
        dynamicPropertiesStore.allowWitnessSortOptimization());
    long voteSum = witnessStandbys.stream().mapToLong(WitnessCapsule::getVoteCount).sum();
    if (voteSum < 1) {
      return;
    }
    long totalPay = dynamicPropertiesStore.getWitness127PayPerBlock();
    double eachVotePay = (double) totalPay / voteSum;
    for (WitnessCapsule w : witnessStandbys) {
      long pay = (long) (w.getVoteCount() * eachVotePay);
      payReward(w.getAddress().toByteArray(), pay);
      logger.debug("Pay {} stand reward {}.", Hex.toHexString(w.getAddress().toByteArray()), pay);
    }
  }

  public void payBlockReward(byte[] witnessAddress, long value) {
    logger.debug("Pay {} block reward {}.", Hex.toHexString(witnessAddress), value);
    payReward(witnessAddress, value);
  }

  public void payTransactionFeeReward(byte[] witnessAddress, long value) {
    logger.debug("Pay {} transaction fee reward {}.", Hex.toHexString(witnessAddress), value);
    payReward(witnessAddress, value);
  }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L79-87)
```java
  private void payReward(byte[] witnessAddress, long value) {
    long cycle = dynamicPropertiesStore.getCurrentCycleNumber();
    int brokerage = delegationStore.getBrokerage(cycle, witnessAddress);
    double brokerageRate = (double) brokerage / 100;
    long brokerageAmount = (long) (brokerageRate * value);
    value -= brokerageAmount;
    delegationStore.addReward(cycle, witnessAddress, value);
    adjustAllowance(witnessAddress, brokerageAmount);
  }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1954-1964)
```java

      if (chainBaseManager.getDynamicPropertiesStore().supportTransactionFeePool()) {
        long transactionFeeReward = floorDiv(
            chainBaseManager.getDynamicPropertiesStore().getTransactionFeePool(),
                Constant.TRANSACTION_FEE_POOL_PERIOD,
            chainBaseManager.getDynamicPropertiesStore().disableJavaLangMath());
        mortgageService.payTransactionFeeReward(witnessCapsule.getAddress().toByteArray(),
            transactionFeeReward);
        chainBaseManager.getDynamicPropertiesStore().saveTransactionFeePool(
            chainBaseManager.getDynamicPropertiesStore().getTransactionFeePool()
                - transactionFeeReward);
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L133-146)
```java
  public void accumulateWitnessVi(long cycle, byte[] address, long voteCount) {
    BigInteger preVi = getWitnessVi(cycle - 1, address);
    long reward = getReward(cycle, address);
    if (reward == 0 || voteCount == 0) { // Just forward pre vi
      if (!BigInteger.ZERO.equals(preVi)) { // Zero vi will not be record
        setWitnessVi(cycle, address, preVi);
      }
    } else { // Accumulate delta vi
      BigInteger deltaVi = BigInteger.valueOf(reward)
          .multiply(DECIMAL_OF_VI_REWARD)
          .divide(BigInteger.valueOf(voteCount));
      setWitnessVi(cycle, address, preVi.add(deltaVi));
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/service/RewardViCalService.java (L215-229)
```java
  private void accumulateWitnessVi(long cycle, byte[] address) {
    BigInteger preVi = getWitnessVi(cycle - 1, address);
    long voteCount = getWitnessVote(cycle, address);
    long reward = getReward(cycle, address);
    if (reward == 0 || voteCount == 0) { // Just forward pre vi
      if (!BigInteger.ZERO.equals(preVi)) { // Zero vi will not be record
        setWitnessVi(cycle, address, preVi);
      }
    } else { // Accumulate delta vi
      BigInteger deltaVi = BigInteger.valueOf(reward)
          .multiply(DECIMAL_OF_VI_REWARD)
          .divide(BigInteger.valueOf(voteCount));
      setWitnessVi(cycle, address, preVi.add(deltaVi));
    }
  }
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L154-162)
```java
    if (dynamicPropertiesStore.allowChangeDelegation()) {
      long nextCycle = dynamicPropertiesStore.getCurrentCycleNumber() + 1;
      dynamicPropertiesStore.saveCurrentCycleNumber(nextCycle);
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.setBrokerage(nextCycle, witness.createDbKey(),
            delegationStore.getBrokerage(witness.createDbKey()));
        delegationStore.setWitnessVote(nextCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }
```
