## Analysis

The reported Morpho bug is about a **mechanism switch (new rewards manager) silently breaking/losing already-accrued, not-yet-claimed rewards** for a subset of claimants because the claim path doesn't know how to read data computed under the old mechanism. Java-tron has a directly analogous mechanism switch: the `ALLOW_NEW_REWARD` governance proposal, which changes vote-reward accounting from a per-cycle percentage algorithm to a cumulative "Vi" (value-index) algorithm.

### Title
Switching to the new reward algorithm (`ALLOW_NEW_REWARD`) silently drops pre-switch accrued vote rewards for smart-contract voters claiming via TVM `withdrawReward`/`rewardBalance` opcodes - (File: `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java`)

### Summary
When the `ALLOW_NEW_REWARD` proposal takes effect, `DynamicPropertiesStore.saveNewRewardAlgorithmEffectiveCycle()` records the cycle at which reward accounting switches from the legacy percentage-of-vote algorithm to a cumulative Vi (`witnessVi`) algorithm. [1](#0-0) [2](#0-1) 

Regular account reward queries/withdrawals go through `MortgageService.computeReward`, which correctly splits the requested cycle range at `newRewardAlgorithmEffectiveCycle`, computing the pre-switch portion with the legacy algorithm (`getOldReward`) and the post-switch portion with the Vi delta algorithm: [3](#0-2) 

However, TVM-triggered reward claims for smart-contract accounts (the `withdrawReward()` and `getRewardBalance()` opcodes) go through `VoteRewardUtil.computeReward`, which has **no such split** - it unconditionally computes the reward for the *entire* `[beginCycle, endCycle)` range using only `delegationStore.getWitnessVi()` deltas: [4](#0-3) 

### Finding Description
`DelegationStore.accumulateWitnessVi` (the live Vi table read by `VoteRewardUtil`) is only ever populated going forward, starting from the cycle in which `DynamicPropertiesStore.useNewRewardAlgorithm()` becomes true, via `MaintenanceManager.doMaintenance`: [5](#0-4) 

Cycles before the switch never get a `witnessVi` entry in `DelegationStore` (that history is only backfilled asynchronously into a *separate* store, `RewardViStore`, by `RewardViCalService`, and that backfilled data is only consulted by `MortgageService`'s `getOldReward` path, not by `VoteRewardUtil`): [6](#0-5) 

So for a smart-contract account that voted and accrued rewards *before* `ALLOW_NEW_REWARD` took effect, and only calls the TVM `withdrawReward()`/`getRewardBalance()` opcode *after* the switch, `VoteRewardUtil.computeReward` will compute `beginVi = getWitnessVi(beginCycle-1) = 0` and `endVi = getWitnessVi(endCycle-1)` (which only reflects Vi accumulated since the switch cycle). The delta therefore excludes all reward accrued in the legacy-algorithm cycles - that portion of the reward is permanently lost, exactly as the external report describes for a rewards-manager swap: old unclaimed rewards become unreachable through the (now-updated) claim path.

### Impact Explanation
This is an accounting-corruption bug: legitimate, already-earned TRX rewards for smart-contract voters become permanently unclaimable once the network votes in `ALLOW_NEW_REWARD`, if the contract had not withdrawn before the switch. Regular (non-contract) accounts are unaffected because they use `MortgageService`, which has the correct migration logic. This creates an inconsistency where TVM-driven claims (`withdrawReward`/`rewardBalance` precompiles, exercised via `TriggerSmartContract`) silently under-report/zero-out reward amounts, resulting in loss of user funds relative to what the chain's own accounting (`MortgageService`/`RewardViCalService`) considers owed.

### Likelihood Explanation
`ALLOW_NEW_REWARD` and `ALLOW_TVM_VOTE` are both real, already-used chain parameters (mainnet has already gone through this or similar algorithm transitions, as evidenced by the hardcoded `MAIN_NET_ROOT_HEX` merkle root in `RewardViCalService`). Any account that voted via a smart contract before such a switch and calls `withdrawReward()`/`getRewardBalance()` afterward - a completely unprivileged, anonymous broadcast transaction (`TriggerSmartContract`) - will trigger the bug. No special permissions are needed to reach the path.

### Recommendation
Give `VoteRewardUtil.computeReward` the same split logic as `MortgageService.computeReward`: for the portion of `[beginCycle, endCycle)` that falls before `dynamicPropertiesStore.getNewRewardAlgorithmEffectiveCycle()`, fall back to the legacy per-cycle percentage computation (or to `RewardViCalService`'s backfilled Vi data) instead of only using the live `DelegationStore` Vi table.

### Proof of Concept
1. Deploy a contract and vote for a witness in cycle N (while `ALLOW_NEW_REWARD` is not yet active), letting rewards accrue for several cycles under the legacy algorithm - reproducible with the existing test harness pattern in `framework/src/test/java/org/tron/common/runtime/vm/VoteTest.java` (`voteWitness`, `payRewardAndDoMaintenance`).
2. Pass the `ALLOW_NEW_REWARD` proposal so `newRewardAlgorithmEffectiveCycle` is set to a cycle after N (`ProposalService.process` -> `saveNewRewardAlgorithmEffectiveCycle`).
3. Advance a few more cycles, then call the contract's `withdrawReward()` (TVM opcode) as in `checkRewardAndWithdraw`/`RewardBalanceTest`.
4. Compare the amount returned/credited against `MortgageService.queryReward(contractAddress)` for the same address/cycle range computed off-chain with the legacy formula - the TVM-obtained amount excludes the pre-switch cycles' reward, demonstrating the loss.

### Citations

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L290-294)
```java
        case ALLOW_NEW_REWARD: {
          manager.getDynamicPropertiesStore().saveNewRewardAlgorithmEffectiveCycle();
          manager.getDynamicPropertiesStore().saveAllowNewReward(entry.getValue());
          break;
        }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L2567-2573)
```java
  public void saveNewRewardAlgorithmEffectiveCycle() {
    if (getNewRewardAlgorithmEffectiveCycle() == Long.MAX_VALUE) {
      long currentCycle = getCurrentCycleNumber();
      this.put(NEW_REWARD_ALGORITHM_EFFECTIVE_CYCLE,
          new BytesCapsule(ByteArray.fromLong(currentCycle + 1)));
    }
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

**File:** chainbase/src/main/java/org/tron/core/service/RewardViCalService.java (L209-229)
```java
  private void accumulateWitnessReward(byte[] witness) {
    long startCycle = 1;
    LongStream.range(startCycle, newRewardCalStartCycle)
        .forEach(cycle -> accumulateWitnessVi(cycle, witness));
  }

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
