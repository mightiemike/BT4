### Title
Witness rewards accumulated via `accumulateWitnessVi` are silently dropped ("lost") when a witness's vote count is zero at the time `doMaintenance` runs, even though the reward budget was already deducted - ([File: chainbase/src/main/java/org/tron/core/store/DelegationStore.java])

### Summary
This is a direct analog of the reported `BabelVault`/`EmissionSchedule` bug class: a value-index-based reward accounting scheme decrements/consumes a reward budget for an entity even when that entity's "voting weight" is zero, so the reward can never be attributed to (claimed by) any voter. In java-tron's new reward algorithm, `MortgageService.payReward` (and `payStandbyWitness`/`payBlockReward`) unconditionally credits a per-cycle, per-witness reward bucket via `DelegationStore.addReward`, while `MaintenanceManager.doMaintenance` later calls `DelegationStore.accumulateWitnessVi(cycle, witness, witness.getVoteCount())`, which explicitly **discards** that cycle's reward from the distributable value index (`Vi`) whenever `voteCount == 0`.

### Finding Description
The reward flow is:

1. During block production, `MortgageService.payReward` adds value into a per-(cycle, witness) reward bucket unconditionally: [1](#0-0) 

2. At the end of the cycle, `MaintenanceManager.doMaintenance` calls `accumulateWitnessVi` for **every** witness using its current `voteCount`: [2](#0-1) 

3. `DelegationStore.accumulateWitnessVi` reads the reward already recorded for that cycle (`getReward(cycle, address)`), but if `voteCount == 0`, it takes the "just forward previous Vi" branch and **never adds the recorded reward into the Vi accumulator**: [3](#0-2) 

4. Voters later withdraw rewards through `MortgageService.withdrawReward` (called transitively by `VoteWitnessActuator.countVoteAccount` and `VoteRewardUtil.withdrawReward` for TVM votes), which computes user rewards strictly from `deltaVi = endVi - beginVi` for the witnesses they voted for: [4](#0-3) [5](#0-4) 

Because a witness's `voteCount` field on the `WitnessCapsule` is only updated once per cycle at maintenance time (via the aggregate `countWitness` map from `VotesStore`), it is entirely possible for a witness to remain in the active (top-27) or standby set — and thus keep earning block/transaction-fee rewards for an entire cycle via `payBlockReward`/`payTransactionFeeReward`/`payStandbyWitness` — while its *effective* vote weight (as reflected by `witness.getVoteCount()` used in `accumulateWitnessVi`) reaches zero by the time `doMaintenance` executes for that cycle, e.g. because all voters withdrew/cleared their votes (`VoteWitnessActuator`, zero-vote re-vote, or `UnfreezeBalanceV2Actuator`/`UnfreezeBalanceActuator` reducing available TRON power to zero) within the same cycle. In that scenario, `getReward(cycle, witness) > 0` (funds already deducted from block-reward/tx-fee budget and recorded) but `voteCount == 0`, so the reward is silently excluded from `Vi` — it is neither paid to any voter (no positive `deltaVi`) nor returned to the emission pool. The value is effectively "lost": it was consumed/decremented from the network's reward budget but never allocated to any beneficiary, exactly matching the `BabelVault` bug class where unallocated supply decreases with zero actual allocation because the emission receiver had no registered voting weight.

Standby-witness rewards additionally have a matching class-level guard (`payStandbyWitness` skips if `voteSum < 1` and filters `voteCount < 1` witnesses in `WitnessStore.getWitnessStandby`), but this only protects the *aggregate* payout list at pay-time — it does not protect the per-witness accumulation path in `accumulateWitnessVi`, which is evaluated later, per-witness, using the vote count as of maintenance time rather than as of pay time: [6](#0-5) [7](#0-6) 

### Impact Explanation
Reward funds that were already deducted from the block-reward/transaction-fee budget (an accounting decrement analogous to `BabelVault.unallocatedTotal`) become permanently unclaimable when a witness's vote weight collapses to zero within the same cycle it is earning rewards. No user can ever redeem these funds via `withdrawReward`/`queryReward`, since the reward computation is entirely driven by `deltaVi`, which never reflects the dropped reward. This is a real, unprivileged-user-reachable accounting/state-divergence issue causing under-distribution of the promised reward pool — value is silently burned from the distributable pool without any corresponding beneficiary, an underpriced/lost-work class impact.

### Likelihood Explanation
Triggering requires only unprivileged user actions: voters normally withdraw or clear their votes (via `VoteWitnessActuator`/`UnfreezeBalanceV2Actuator`) for reasons unrelated to attacking the system, and the timing (voting weight dropping to zero mid-cycle for a currently-earning witness) is a natural consequence of live TRX unfreeze/vote-clear activity rather than a contrived edge case. The condition does not require any privileged role, and the affected witness need not cooperate — any voter or set of voters withdrawing votes on a low-vote witness can trigger it.

### Recommendation
In `DelegationStore.accumulateWitnessVi` (and the duplicate logic in `RewardViCalService.accumulateWitnessVi`), when `voteCount == 0` but `reward > 0`, do not silently drop the reward: either (a) roll the un-distributable reward forward into the next cycle's reward bucket for the same witness so it can be distributed once `voteCount` becomes nonzero again, or (b) return the amount to the general reward pool / transaction fee pool instead of letting `Vi` simply forward unchanged while the reward value disappears. This mirrors the recommended fix pattern from the original report: detect the zero-weight condition before consuming/recording the reward, and avoid mutating accounting state (analogous to `lockWeeks`) that implies the reward was distributed when it was not.

### Proof of Concept
Conceptually, extend `framework/src/test/java/org/tron/core/services/DelegationServiceTest.java` or `ComputeRewardTest.java` style tests:
1. Enable `useNewRewardAlgorithm` / `allowChangeDelegation` (as in `DelegationServiceTest.test`).
2. Register a witness `W` with `voteCount = 0` (no voter has voted for it yet, or all its voters previously withdrew).
3. Have `W` produce a block, causing `MortgageService.payBlockReward` → `payReward` → `delegationStore.addReward(cycle, W, value)` to record a positive reward for the current cycle for `W`.
4. Call `maintenanceManager.doMaintenance()`. Confirm via `delegationStore.getReward(cycle, W) > 0` yet `delegationStore.getWitnessVi(cycle, W)` remains unchanged from `getWitnessVi(cycle-1, W)` (verifying `accumulateWitnessVi`'s zero-vote branch fired, per `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` lines 133-146).
5. Confirm no account can ever receive this reward via `mortgageService.withdrawReward`/`queryReward`, since no account has `W` in its vote list with a positive `deltaVi` for that cycle — the recorded reward is permanently stranded.

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L53-67)
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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L199-230)
```java
  private long computeReward(long beginCycle, long endCycle, AccountCapsule accountCapsule) {
    if (beginCycle >= endCycle) {
      return 0;
    }

    long reward = 0;
    long newAlgorithmCycle = dynamicPropertiesStore.getNewRewardAlgorithmEffectiveCycle();
    List<Pair<byte[], Long>> srAddresses = accountCapsule.getVotesList().stream()
        .map(vote -> new Pair<>(vote.getVoteAddress().toByteArray(), vote.getVoteCount()))
        .collect(Collectors.toList());
    if (beginCycle < newAlgorithmCycle) {
      long oldEndCycle = min(endCycle, newAlgorithmCycle,
          dynamicPropertiesStore.disableJavaLangMath());
      reward = getOldReward(beginCycle, oldEndCycle, srAddresses);
      beginCycle = oldEndCycle;
    }
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

**File:** actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java (L90-110)
```java
  private static long computeReward(long beginCycle, long endCycle,
                                    AccountCapsule accountCapsule, Repository repository) {
    if (beginCycle >= endCycle) {
      return 0;
    }

    long reward = 0;
    for (Protocol.Vote vote : accountCapsule.getVotesList()) {
      byte[] srAddress = vote.getVoteAddress().toByteArray();
      BigInteger beginVi = repository.getDelegationStore().getWitnessVi(beginCycle - 1, srAddress);
      BigInteger endVi = repository.getDelegationStore().getWitnessVi(endCycle - 1, srAddress);
      BigInteger deltaVi = endVi.subtract(beginVi);
      if (deltaVi.signum() <= 0) {
        continue;
      }
      long userVote = vote.getVoteCount();
      reward += deltaVi.multiply(BigInteger.valueOf(userVote))
          .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
    }
    return reward;
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/WitnessStore.java (L44-56)
```java
  public List<WitnessCapsule> getWitnessStandby(boolean isSortOpt) {
    List<WitnessCapsule> ret;
    List<WitnessCapsule> all = getAllWitnesses();
    sortWitnesses(all, isSortOpt);
    if (all.size() > Parameter.ChainConstant.WITNESS_STANDBY_LENGTH) {
      ret = new ArrayList<>(all.subList(0, Parameter.ChainConstant.WITNESS_STANDBY_LENGTH));
    } else {
      ret = new ArrayList<>(all);
    }
    // trim voteCount = 0
    ret.removeIf(w -> w.getVoteCount() < 1);
    return ret;
  }
```
