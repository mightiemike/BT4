### Title
Repeated floor-division in per-cycle reward Vi accumulation and voter reward computation permanently locks/burns TRX voting rewards - (File: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java`)

### Summary
The Vote/Delegation reward-distribution pipeline (`DelegationStore.accumulateWitnessVi`, `RewardViCalService.accumulateWitnessVi`, `MortgageService.computeReward`, and `VoteRewardUtil.computeReward`) splits a witness's per-cycle reward pot among all voters proportionally to each voter's vote weight, using two sequential integer (floor) divisions. As in the reported Debita bug class, splitting a fixed pot by percentage/proportional share with rounding-down at each recipient leaves a residual dust amount that is never credited to any account and can never be claimed by anyone, effectively burning it every cycle.

### Finding Description
When a witness earns a reward in a cycle, `MortgageService.payReward` records the total reward for that witness/cycle via `delegationStore.addReward(cycle, witnessAddress, value)` [1](#0-0) .

At maintenance time, this per-cycle reward pot is converted into a "Vi" delta that will later be used to compute each voter's proportional share:
```
BigInteger deltaVi = BigInteger.valueOf(reward)
    .multiply(DECIMAL_OF_VI_REWARD)
    .divide(BigInteger.valueOf(voteCount));
``` [2](#0-1) 

This same floor-division pattern is duplicated in `RewardViCalService.accumulateWitnessVi` [3](#0-2) .

The division `reward * 1e18 / voteCount` truncates any fractional remainder — the pot is "spread" over `voteCount` vote-units at a rate that is rounded down.

Later, when an individual voter withdraws/queries their reward, their share is computed with a second floor division:
```
reward += deltaVi.multiply(BigInteger.valueOf(userVote))
    .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
```
This appears in `MortgageService.computeReward` [4](#0-3) , and identically in `VoteRewardUtil.computeReward` (the TVM-vote precompile path) [5](#0-4) .

Because `deltaVi` was already floored once (losing the sub-`DECIMAL_OF_VI_REWARD` remainder of `reward/voteCount`), and each voter's share is floored a second time when multiplying back by their `userVote`, the sum of all voters' claimed rewards for that cycle is generally strictly less than the `reward` value originally recorded by `addReward`. Unlike the transaction-fee pool (`Manager.payReward`, which retains the floor-division remainder in `TransactionFeePool` for future periods) [6](#0-5) , there is no store or mechanism that tracks or redistributes the dust lost by the Vi mechanism — the reward pot recorded via `addReward` is never decremented/reconciled against what voters actually manage to claim, so the shortfall is silently and permanently unrecoverable by anyone (witness, voter, or the protocol treasury).

### Impact Explanation
Every voting cycle for every witness that has voters, a small amount of TRX reward becomes permanently unclaimable/lost due to compounding rounding-down in `deltaVi` calculation and per-voter share extraction. This is directly analogous to the reported class of bug: a fixed reward "pot" distributed proportionally among many participants via percentage/ratio truncation, where the sum of floored individual shares is less than the pot, and the shortfall is never recoverable. Over the life of the chain, across many cycles, witnesses, and voters, this compounds into a measurable, permanent loss of reward funds for voters (each voter with a vote count that doesn't divide evenly into `reward/voteCount * 1e18` receives fractionally less than their true proportional entitlement) and no party can reclaim it.

### Likelihood Explanation
This occurs automatically and unconditionally on every maintenance cycle for every witness with `reward > 0 && voteCount > 0`, and on every voter reward computation (`WithdrawRewardProcessor`/`MortgageService.withdrawReward`, reachable via a normal `WithdrawBalanceContract` transaction or the TVM `withdrawreward` native contract) whenever `userVote` is not an exact multiple relationship with `voteCount` after the `deltaVi` truncation. Given typical vote counts (arbitrary integers, not powers matching `1e18`/`voteCount` cleanly), this is essentially guaranteed to occur every cycle at scale — no adversarial action or privileged access is required; it is a deterministic consequence of routine consensus reward accounting.

### Recommendation
Track the reward remainder that is lost in `accumulateWitnessVi`/`computeReward` (e.g., carry forward the unaccumulated remainder of `reward mod voteCount` scaled by `DECIMAL_OF_VI_REWARD` into the next cycle's `deltaVi` computation for that witness, rather than discarding it), and/or reconcile the per-voter floor to guarantee the sum of distributed shares equals the recorded `reward` value (e.g., use higher intermediate precision and only truncate once at the very end, or credit the last claimant/witness with the residual). This mirrors the general mitigation suggested in the source report — preserve or redistribute the rounding remainder rather than letting it be silently and permanently lost.

### Proof of Concept
Given a witness `W` in cycle `c` where:
- `reward = 10` (sun) recorded via `addReward(c, W, 10)`
- 3 voters each with `userVote = 1` (so `voteCount = 3`)

Step 1 — `accumulateWitnessVi`:
```
deltaVi = (10 * 10^18) / 3 = 3333333333333333333   // floor, loses 0.333...e18
``` [7](#0-6) 

Step 2 — each voter's `computeReward`:
```
share = deltaVi * 1 / 10^18 = 3   // per voter, floored again
``` [8](#0-7) 

Total claimed by all 3 voters = `3 + 3 + 3 = 9`, versus the `10` sun originally recorded as the witness's reward pot for that cycle. `1` sun is permanently unaccounted for and unclaimable by anyone — it is neither credited to any voter's allowance, nor retained in any store for later redistribution, unlike the `TransactionFeePool` remainder handling elsewhere in `Manager.payReward`.

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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L215-227)
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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1955-1965)
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
      }
```
