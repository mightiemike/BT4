### Title
Witness reward recorded as distributed to voters even when a witness has zero votes, permanently orphaning the reward share — ([File: chainbase/src/main/java/org/tron/core/store/DelegationStore.java])

### Summary
`DelegationStore.accumulateWitnessVi()` mirrors the Synthetix-style "reward accrues over time regardless of stake" pattern flagged in the external report. When a witness's vote weight for a cycle is zero, the reward that was already recorded for that witness/cycle via `MortgageService.payReward()` → `DelegationStore.addReward()` is silently dropped instead of being carried forward or refunded, so it can never be claimed by any voter.

### Finding Description
`MortgageService.payReward()` unconditionally records a voter-reward-pool value for every cycle in which a witness produces a block or is credited a transaction-fee share, independent of whether the witness currently has any voters: [1](#0-0) 

That value is persisted per-cycle in `DelegationStore`: [2](#0-1) 

Every maintenance cycle, `MaintenanceManager.doMaintenance()` calls `accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount())` for **every** witness, using the live vote-count field that reflects the votes the witness held for the cycle that just ended: [3](#0-2) 

Inside `accumulateWitnessVi`, if `voteCount == 0` for that cycle, the function takes the "just forward pre vi" branch and does **not** fold the recorded reward into the Vi (value-per-vote index): [4](#0-3) 

Because voter rewards are only ever paid out proportionally to `deltaVi` (computed strictly from the `getReward(cycle, address)` value at that specific cycle), skipping the Vi update for a zero-vote cycle means the reward amount recorded for that cycle is never folded into any future Vi delta. It is not deferred or retried later — the next cycle's Vi delta is computed from `getReward(nextCycle, address)`, an independent value. The reward amount tied to the zero-vote cycle is therefore permanently unreachable by `computeReward()` used in both `MortgageService.withdrawReward/queryReward` and `VoteRewardUtil.withdrawReward/queryReward`: [5](#0-4) [6](#0-5) 

This is the same root-cause pattern as the reported bug: the accounting layer records reward as "already distributed" for a period regardless of whether any staker (voter) existed to receive it, and the special-case for the zero-stake condition (`voteCount == 0`) causes the reward for that interval to be lost rather than deferred to when stake reappears.

### Impact Explanation
The net-of-brokerage reward portion (the share intended for voters) accrued by a witness during any cycle in which that witness had zero effective votes is permanently unclaimable by anyone. This is a genuine, if narrow, resource/reward-accounting corruption bug: value that the protocol's own bookkeeping (`getReward`) shows as owed to voters is silently discarded instead of being retained, refunded to the witness, or rolled forward. It does not directly corrupt consensus, but it is a real economic loss/inconsistency in the network's reward-distribution accounting, reachable without any privileged role — any account can register as a witness via a normal `WitnessCreateContract` transaction, and any voter can freely unvote a witness down to zero votes via a normal `VoteWitnessContract` transaction.

### Likelihood Explanation
Triggering this requires a witness to be active (part of the standby/active set that receives block or fee rewards) while having zero recorded votes for the cycle in question. On a mature, competitive mainnet with far more candidate witnesses than available slots this is unlikely, but it is readily reachable on any network where the number of registered witnesses is at or below the slot count (private chains, test networks, or early-stage networks), and can also occur transiently on any network if all voters fully unvote a currently-active witness mid-cycle (vote-count changes only take effect at the next maintenance boundary, so the witness keeps producing/earning rewards with the old, now-zero, vote weight for the rest of the cycle). Both witness creation and unvoting are unprivileged operations available to anonymous accounts submitting ordinary broadcast transactions.

### Recommendation
In `DelegationStore.accumulateWitnessVi()`, when `voteCount == 0` but `reward != 0`, do not silently drop the recorded reward. Instead, either (a) roll the un-distributable reward forward to be added to the next cycle's reward pool for that witness (`addReward(cycle + 1, address, reward)`), or (b) return the funds to the witness's own allowance rather than leaving it as an orphaned value in a cycle whose Vi will never reflect it. This mirrors the report's proposed fix of ensuring rewards are only "distributed" (folded into the claimable index) when there is an actual stake base to receive them, and otherwise explicitly reconciling the funds instead of leaving them unaccounted for.

### Proof of Concept
1. Register a new witness `W` via `WitnessCreateContract` (or use a network where registered witnesses ≤ available SR/standby slots) so `W` becomes part of the active or standby set with `voteCount == 0`.
2. Allow `W` to produce a block, or receive standby pay / transaction-fee share, while `voteCount == 0`: `MortgageService.payBlockReward`/`payTransactionFeeReward` → `payReward` → `DelegationStore.addReward(cycle, W, value)` records a nonzero reward for `cycle`.
3. At the next maintenance, `MaintenanceManager.doMaintenance()` calls `DelegationStore.accumulateWitnessVi(cycle, W, 0)` (since `witness.getVoteCount() == 0`), which takes the "forward pre vi" branch and never folds `getReward(cycle, W)` into any Vi delta.
4. Even if voters later vote for `W` in subsequent cycles, `computeReward()` only ever looks at `deltaVi` between the voter's begin/end cycle boundaries computed from future Vi values — the cycle-`cycle` reward for `W` is never included in any voter's claim, and `MortgageService.withdrawReward` never pays it out. The recorded reward value is permanently orphaned.

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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L199-229)
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
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L35-53)
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

  public long getReward(long cycle, byte[] address) {
    BytesCapsule bytesCapsule = get(buildRewardKey(cycle, address));
    if (bytesCapsule == null) {
      return 0L;
    } else {
      return ByteArray.toLong(bytesCapsule.getData());
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
