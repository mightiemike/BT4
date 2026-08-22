## Title
Voter rewards silently forfeited when a witness accrues block/standby rewards for a cycle with zero recorded votes - (`chainbase/src/main/java/org/tron/core/store/DelegationStore.java`)

### Summary
java-tron's witness-vote reward mechanism accrues block rewards to a witness for the *current cycle* independent of whether that witness has any recorded voters for the cycle, then later distributes that accrued amount to voters proportionally via a per-vote index (`Vi`). When a witness's recorded vote count for a cycle is `0`, the reward-index update step forwards the previous index unchanged instead of folding the newly accrued reward into it, so the reward that was already credited for that cycle becomes permanently unclaimable by any voter — mirroring the "bribe rewards notified for an epoch with no depositors are lost" bug class in the referenced report.

### Finding Description
Every block, `MortgageService.payReward()` unconditionally credits reward to the block-producing (or standby) witness for the *current* cycle via `delegationStore.addReward(cycle, witnessAddress, value)`, regardless of that witness's vote count for the cycle: [1](#0-0) 

This is invoked from `payBlockReward`/`payTransactionFeeReward`/`payStandbyWitness`, all of which are executed on every block as part of normal, unprivileged consensus processing: [2](#0-1) 

At maintenance time (`MaintenanceManager.doMaintenance()`), for every registered witness the accrued reward is folded into the cumulative per-vote reward index (`Vi`) using that witness's *current* recorded vote count: [3](#0-2) 

The index update logic in `DelegationStore.accumulateWitnessVi()` explicitly skips folding the reward into the index whenever the vote count is zero — it only "forwards" the previous index: [4](#0-3) 

Because voter reward computation (`MortgageService.computeReward`, new algorithm) is driven exclusively by the delta of this `Vi` index between the voter's begin/end cycle: [5](#0-4) 

...any reward that was added via `addReward(cycle, witnessAddress, value)` for a cycle in which the witness's recorded vote count was `0` never produces a nonzero `deltaVi`, so it can never be paid out to any voter. The same loss occurs on the legacy (pre-new-algorithm) path, which explicitly `continue`s (skips) reward distribution whenever `totalVote == 0`: [6](#0-5) 

The identical skip-on-zero-vote logic is duplicated in `RewardViCalService.accumulateWitnessVi()` (used for historical/backfill VI computation), confirming this is the intended (but flawed) design rather than an isolated typo: [7](#0-6) 

The raw value stored by `addReward` under the `cycle-address-reward` key is never referenced again once the zero-vote-count branch is taken — it is orphaned in the store exactly like the bribe-vault tokens in the referenced report that are stuck in `tokenRewardsPerEpoch[token][epochStart]` for an epoch with no depositors.

### Impact Explanation
Reward value intended to be paid pro-rata to a witness's voters is silently and permanently forfeited whenever a witness's recorded vote count for a cycle is `0` while it still received block/standby-witness reward that cycle (e.g. a witness whose voters fully withdraw/unfreeze their stake, or a chain with fewer registered witnesses than the 27/127 slots so some active/standby witnesses have zero votes). This is a reward-accounting correctness issue: value that should ultimately flow to TRX holders who voted is instead lost with no path to recovery, unlike the brokerage share which is credited immediately and unconditionally via `adjustAllowance`. This falls under resource/reward accounting corruption.

### Likelihood Explanation
Low/medium — it requires a witness to keep earning block or standby rewards during a cycle in which its tallied vote count for that cycle is zero. This is realistic on smaller/private networks (fewer registered candidate witnesses than available active/standby slots) and can also arise transiently on any network if all voters for an active witness unfreeze/withdraw their votes within a cycle window while the witness is still in the active/standby set from the prior maintenance tally. No privileged role is required — voting and unfreezing are ordinary broadcast transactions (`VoteWitnessContract`, `WithdrawBalanceContract`/unfreeze contracts).

### Recommendation
When `accumulateWitnessVi` observes `voteCount == 0` for a cycle but `reward > 0`, do not silently forward the previous `Vi` unchanged. Instead, either (a) roll the orphaned reward forward into the next cycle's reward pool for that witness (analogous to a `renotifyRewardAmount`), or (b) redirect it to the witness's own allowance/brokerage, or (c) redirect it to a protocol-level fund, so the value is not permanently unaccounted for.

### Proof of Concept
1. Register/observe a witness `W` that is part of the active or standby set for cycle `C` but has `WitnessCapsule.getVoteCount() == 0` for that cycle (e.g., all voters call unfreeze/withdraw contracts prior to the cycle's vote tally, or a private-network deployment with fewer real candidates than slots).
2. During cycle `C`, `W` produces blocks / is included in standby payout, causing `MortgageService.payBlockReward`/`payTransactionFeeReward`/`payStandbyWitness` to call `payReward`, which calls `delegationStore.addReward(C, W, value)` with `value > 0` (see `MortgageService.java:79-87`).
3. At the maintenance boundary, `MaintenanceManager.doMaintenance()` calls `delegationStore.accumulateWitnessVi(C, W, 0)` since `W.getVoteCount() == 0` (`MaintenanceManager.java:96-101`).
4. Inside `accumulateWitnessVi`, because `voteCount == 0`, the branch at `DelegationStore.java:136-139` is taken: the previous `Vi` is simply forwarded/unchanged; the reward added in step 2 is never folded in.
5. Any voter querying/withdrawing rewards via `MortgageService.computeReward` computes `deltaVi = 0` for cycle `C` (since `Vi` didn't change), so the reward accrued in step 2 is never paid to anyone and remains permanently orphaned in the `reward` key of `DelegationStore`.

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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L171-188)
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
  }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L215-230)
```java
    if (beginCycle < endCycle) {
      for (Pair<byte[], Long>  vote : srAddresses) {
        byte[] srAddress = vote.getKey();
        BigInteger beginVi = delegationStore.getWitnessVi(beginCycle - 1, srAddress);
        BigInteger endVi = delegationStore.getWitnessVi(endCycle - 1, srAddress);
        BigInteger deltaVi = endVi.subtract(beginVi);
        if (deltaVi.signum() <= 0) {
          continue;
        }
        long userVote = vote.getValue();
        reward += deltaVi.multiply(BigInteger.valueOf(userVote))
            .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
      }
    }
    return reward;
  }
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
