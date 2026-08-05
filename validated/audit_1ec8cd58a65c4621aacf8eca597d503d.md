### Title
Witness block/transaction-fee rewards are permanently lost when a witness's vote count is zero at cycle-boundary accounting time - ([File: chainbase/src/main/java/org/tron/core/store/DelegationStore.java])

### Summary
This is a direct analog of the Blend M-07 finding: rewards are "gulped" into a shared bucket regardless of whether the underlying supply/weight (here, vote count) is zero, while the withdrawal/claim path explicitly skips distribution when that weight is zero — permanently stranding the funds that were already accrued.

### Finding Description
Each block, `MortgageService#payReward` unconditionally credits a witness's per-cycle reward bucket via `DelegationStore#addReward(cycle, witnessAddress, value)`, regardless of that witness's current vote count: [1](#0-0) 

At every maintenance boundary, `MaintenanceManager#doMaintenance` propagates the accrued per-cycle reward into the voter-index (`vi`) accounting used by the "new reward algorithm", using the witness's vote count *at that moment*: [2](#0-1) 

This calls `DelegationStore#accumulateWitnessVi`, which only advances the vi (the accumulator later used to compute each voter's share of the reward) when `voteCount != 0`. If `voteCount == 0`, the previously accumulated `preVi` is simply carried forward unchanged, and the reward amount that was already added via `addReward` for that cycle is dropped from the vi calculation entirely — with no fallback or refund path: [3](#0-2) 

The old-algorithm claim path (`MortgageService#computeReward`) exhibits the identical pattern: it explicitly `continue`s (skips) any witness whose recorded `totalVote` for the cycle is `0` or `REMARK`, so the reward bucket populated by `payReward`/`addReward` for that witness/cycle is never distributed to any voter: [4](#0-3) 

The vote-count snapshot stored per cycle (`setWitnessVote`) is taken for *all* witnesses (not just the actively scheduled ones) at maintenance time, using the post-vote-counting value, while block-production reward crediting (`payReward`) happens continuously throughout the cycle based on whichever witness the DPoS schedule picks to produce a block: [5](#0-4) 

Because the active/scheduled witness set is only recomputed at maintenance boundaries, a witness can end up producing blocks (and thus receiving `payBlockReward`/`payTransactionFeeReward` credits) for part of a cycle in which its snapshotted vote count is (or becomes) `0` — e.g., a witness whose voters fully unvote mid-cycle, or, in private/consortium/test networks that run with fewer registered witnesses than the active-witness slot count, a newly registered SR with no votes yet that is immediately scheduled to produce blocks. In all such cases, the TRX credited via `addReward` for that witness/cycle can never be attributed to any voter's `vi` delta or `totalVote`-based share, so it is permanently unclaimable — effectively burned from circulation while still having been deducted from `getWitnessPayPerBlock()` / `TransactionFeePool` accounting.

### Impact Explanation
This is a fund-loss/accounting divergence bug: TRX that is correctly deducted from the transaction fee pool / block-reward budget and credited into the per-cycle witness reward bucket becomes permanently unclaimable by any account once the corresponding vote-count snapshot for that witness/cycle is zero. The result is a silent, protocol-level loss that no unprivileged user (voter or witness) can recover, matching the "accounting" and "invalid-state" impact classes.

### Likelihood Explanation
Likelihood is highest on private/consortium/test TRON networks (or a chain with fewer registered witnesses than the standby/active slot count), where witnesses with zero votes are still scheduled to produce blocks. On mainnet-scale networks with many voters it requires a witness's total votes to reach exactly zero during an active cycle (full unvote), which is a plausible but less frequent edge case — the same likelihood argument accepted for the original Blend finding (reserves reaching 0 supply mid-cycle).

### Recommendation
When crediting rewards via `MortgageService#payReward` (or when propagating rewards via `accumulateWitnessVi`), check the witness's current effective vote count; if it is zero, either (a) redirect/withhold the reward until the witness regains votes without silently discarding it (e.g., store it separately and add it to the next non-zero-vote cycle's vi calculation instead of overwriting `preVi` unchanged), or (b) prevent zero-vote witnesses from being scheduled/rewarded in the first place. The claim-side skip (`totalVote == 0 → continue`) should be paired with a guarantee that the corresponding reward was never gulped into that bucket to begin with.

### Proof of Concept
1. Configure a witness `W` (or use a private-network genesis witness) such that its vote count becomes `0` for a given cycle `N` (e.g., via full unvote through `WithdrawBalanceContract`/`WoteWitnessContract`, or run a private chain with witness count below the active slot count so an unvoted witness is still scheduled).
2. Ensure `W` still produces at least one block in cycle `N` (DPoS scheduling only updates at maintenance boundaries), triggering `Manager#payReward` → `MortgageService#payBlockReward`/`payTransactionFeeReward` → `DelegationStore#addReward(N, W, value)`.
3. At the next maintenance boundary, observe `MaintenanceManager#doMaintenance` calling `DelegationStore#accumulateWitnessVi(N, W, 0)` (old algorithm: `DelegationStore#setWitnessVote(N+1, W, 0)`).
4. Confirm via `DelegationStore#getWitnessVi` (new algorithm) that the `vi` for cycle `N` is unchanged from `preVi` (reward dropped), or via `MortgageService#computeReward` (old algorithm) that the `totalVote == 0` branch causes the accumulated reward for `W`/cycle `N` to be skipped for every voter.
5. Verify no account ever receives the TRX that was credited in step 2, confirming permanent loss.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L171-187)
```java
  private long computeReward(long cycle, List<Pair<byte[], Long>> votes) {
    long reward = 0;
    for (Pair<byte[], Long> vote : votes) {
      byte[] srAddress = vote.getKey();
      long totalReward = delegationStore.getReward(cycle, srAddress);
      if (totalReward <= 0) {
        continue;
      }
      long totalVote = delegationStore.getWitnessVote(cycle, srAddress);
      if (totalVote == DelegationStore.REMARK || totalVote == 0) {
        continue;
      }
      long userVote = vote.getValue();
      double voteRate = (double) userVote / totalVote;
      reward += voteRate * totalReward;
    }
    return reward;
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L94-101)
```java
    DynamicPropertiesStore dynamicPropertiesStore = consensusDelegate.getDynamicPropertiesStore();
    DelegationStore delegationStore = consensusDelegate.getDelegationStore();
    if (dynamicPropertiesStore.useNewRewardAlgorithm()) {
      long curCycle = dynamicPropertiesStore.getCurrentCycleNumber();
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount());
      });
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
