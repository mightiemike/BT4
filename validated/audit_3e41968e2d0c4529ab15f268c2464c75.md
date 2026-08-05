## Title
Witness voting rewards become permanently unclaimable when a witness has zero votes - ([File: chainbase/src/main/java/org/tron/core/store/DelegationStore.java])

### Summary
`MortgageService.payReward` credits the non-brokerage portion of every block/transaction-fee reward to a per-cycle, per-witness reward pool via `DelegationStore.addReward`, intended to be split among the witness's voters. During maintenance, `MaintenanceManager.doMaintenance` converts that per-cycle reward into a cumulative "Vi" (value index) used to compute each voter's share. If a witness has **zero votes** in a given cycle, the reward that was already accrued for that cycle is silently dropped instead of being distributed or refunded — a direct analog of the "InfraredVault rewards lost when there are no stakers" bug class, where reward emissions continue to accrue but cannot be claimed by anyone once there are no participants to receive them.

### Finding Description
Every block reward and transaction fee reward paid to an active witness goes through `payReward`, which unconditionally adds the (non-brokerage) reward into the cycle's reward bucket for that witness address, regardless of how many votes it currently has: [1](#0-0) 

That reward is stored via `DelegationStore.addReward(cycle, address, value)`: [2](#0-1) 

At each maintenance cycle, `MaintenanceManager.doMaintenance` converts the accrued reward into the cumulative Vi index used by voters to compute their share, using the witness's **current** vote count: [3](#0-2) 

The conversion logic in `DelegationStore.accumulateWitnessVi` explicitly discards the reward when `voteCount == 0` — it forwards the previous Vi unchanged and never folds the newly accrued reward into it: [4](#0-3) 

Because the Vi is not incremented, no voter's `delta Vi * userVote` computation (in `VoteRewardUtil.computeReward` / `MortgageService.computeReward`) will ever reflect that cycle's reward — it is permanently orphaned in the `delegationStore` reward record with no code path to reclaim, redistribute, or refund it: [5](#0-4) 

The same structural issue exists in the pre-Vi ("old") reward algorithm, where `computeReward(cycle, votes)` in `MortgageService` skips (and thus loses) any reward recorded for a witness whose `totalVote` is `0` or `REMARK`: [6](#0-5) 

This mirrors the reported MultiRewards flaw exactly: the reward-distribution mechanism keeps accruing/streaming rewards on a schedule (per block / per cycle) independent of whether there are any current "stakers" (voters) to receive them, and once the participant count hits zero, that period's rewards are unrecoverably lost.

### Impact Explanation
Any TRX reward credited to a witness during a cycle in which that witness has zero votes is permanently lost — it is neither paid to the witness (which already received its brokerage cut separately via `adjustAllowance`), nor to any voter, nor recoverable by governance/black-hole burn accounting. This is a real fund-loss / accounting bug: value is implicitly removed from circulation without being tracked as burned, unlike the deliberate black-hole optimizations elsewhere in the codebase. The severity is moderated by the fact that TRON's active-witness selection generally requires non-trivial votes to remain in the top set, but a witness can transiently drop to zero votes (e.g., all voters call `VoteWitnessActuator`/`UnfreezeBalance` to redirect or clear their votes) within a cycle before the next maintenance re-selects the active witness set, causing that cycle's reward to be lost.

### Likelihood Explanation
Low to Medium. It requires a witness's total vote count to be reduced to exactly zero while it is still part of the active/standby producing set for at least one reward-accruing cycle — plausible when large voters mass-unvote a witness (e.g., due to slashing news, migration, or a witness stepping down) faster than the maintenance cycle re-ranks the active set. Reachable through ordinary, unprivileged actuators (`VoteWitnessActuator`, `UnfreezeBalanceContract`/`UnfreezeBalanceV2Contract`) with no special permissions needed.

### Recommendation
When `accumulateWitnessVi` (or the old-algorithm `computeReward`) encounters `voteCount == 0` (or `totalVote == 0`) for a cycle that has a non-zero `reward`, do not silently discard the reward. Instead, either roll it forward to be included with the next cycle in which the witness has non-zero votes, or credit it to the witness's own allowance/brokerage, or route it to the existing black-hole/burn accounting so the loss is at least tracked and consistent with the rest of the protocol's supply accounting.

### Proof of Concept
1. Witness `W` is actively producing blocks and accumulates votes from voter `V`.
2. `V` calls `VoteWitnessActuator`/`UnfreezeBalanceV2Contract` to clear all votes for `W`, bringing `W`'s `voteCount` to 0, while `W` remains in the active witness set for the rest of the current cycle (active set only refreshed at next maintenance in `MaintenanceManager.doMaintenance`, lines 84-101).
3. `W` continues to produce blocks; `MortgageService.payBlockReward`/`payTransactionFeeReward` → `payReward` credits `delegationStore.addReward(cycle, W, value)` for each block.
4. At the next maintenance, `DelegationStore.accumulateWitnessVi(cycle, W, 0)` is invoked with `voteCount == 0`; since `reward > 0` but `voteCount == 0`, the method takes the "just forward pre vi" branch and the reward is never folded into `Vi`.
5. No voter's `computeReward` call (via `MortgageService.withdrawReward`/`queryReward` or `VoteRewardUtil`) can ever recover this cycle's reward — it is permanently lost from circulation with no burn/black-hole accounting entry.

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

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L35-44)
```java
  public void addReward(long cycle, byte[] address, long value) {
    byte[] key = buildRewardKey(cycle, address);
    BytesCapsule bytesCapsule = get(key);
    if (bytesCapsule == null) {
      put(key, new BytesCapsule(ByteArray.fromLong(value)));
    } else {
      put(key, new BytesCapsule(ByteArray
          .fromLong(ByteArray.toLong(bytesCapsule.getData()) + value)));
    }
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
