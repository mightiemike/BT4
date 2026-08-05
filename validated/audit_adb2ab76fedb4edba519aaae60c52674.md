## Analog Vulnerability Found

### Title
Division-before-multiplication in legacy vote reward calculation causes reward precision loss/rounding errors - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
`MortgageService.computeReward(long cycle, List<Pair<byte[], Long>> votes)` computes a voter's share of a witness's block/transaction-fee reward pool by first dividing `userVote` by `totalVote` (as a `double`) and only then multiplying by `totalReward`, instead of multiplying first and dividing last. This is the same bug class flagged in the external report for `GlpPricing.sol`'s `glpToUsd` — dividing before multiplying discards precision that the subsequent multiplication can no longer recover, and the result is truncated when cast back to `long`.

### Finding Description
In the legacy (pre-VI) reward algorithm: [1](#0-0) 

```java
long userVote = vote.getValue();
double voteRate = (double) userVote / totalVote;
reward += voteRate * totalReward;
```

`voteRate` is computed by dividing `userVote / totalVote` first. Any fractional precision beyond `double`'s significand, or any rounding introduced during the division step, is baked into `voteRate` before it is ever multiplied by `totalReward`. This is the mathematically inferior ordering compared to `(userVote * totalReward) / totalVote`, which defers the lossy division until after the multiplication and preserves more precision. This exact "multiply-before-divide" fix pattern is what was applied elsewhere in this same codebase — e.g. the newer VI-based reward path used by `computeReward(long beginCycle, long endCycle, AccountCapsule accountCapsule)`: [2](#0-1) 

```java
reward += deltaVi.multiply(BigInteger.valueOf(userVote))
    .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
```

and in `RewardViCalService`/`VoteRewardUtil`, which all correctly multiply first using `BigInteger` before dividing. The legacy `computeReward(long cycle, ...)` path was never upgraded to this pattern and is still invoked via `getOldReward`: [3](#0-2) 

```java
private long getOldReward(long begin, long end, List<Pair<byte[], Long>> votes) {
  if (dynamicPropertiesStore.allowOldRewardOpt()) {
    return rewardViCalService.getNewRewardAlgorithmReward(begin, end, votes);
  }
  long reward = 0;
  for (long cycle = begin; cycle < end; cycle++) {
    reward += computeReward(cycle, votes);
  }
  return reward;
}
```

This is reachable from both `withdrawReward` and `queryReward`, which are the public entry points invoked by `WithdrawBalanceActuator` and the TVM native `WithdrawRewardProcessor`/`VoteRewardUtil` — actions any unprivileged TRX holder who has voted for a witness can trigger. [4](#0-3) [5](#0-4) 

### Impact Explanation
This affects on-chain accounting: the amount of allowance/reward credited to a voter's account can be systematically miscalculated (typically truncated downward) versus the mathematically correct `userVote * totalReward / totalVote`, because the division that determines `voteRate` happens before the multiplication. Over many voters and cycles this can produce a persistent discrepancy between the sum of rewards paid out and the total reward pool recorded for a witness/cycle, i.e., a state/accounting divergence in a user-facing settlement flow (reward withdrawal).

### Likelihood Explanation
This code path only executes for cycles prior to `newRewardAlgorithmEffectiveCycle` and only when `allowOldRewardOpt()` is disabled (i.e., the chain has not switched to the newer `rewardViCalService`-based algorithm for those historical cycles). Where applicable, it triggers deterministically and automatically whenever any voter calls `withdrawReward`/`queryReward` (via `WithdrawBalanceActuator` or the TVM native reward-withdraw path) for a period spanning pre-migration cycles — no special privileges are required to trigger it, only ordinary voting/withdrawal activity.

### Recommendation
Replace the division-then-multiplication in `computeReward(long cycle, List<Pair<byte[], Long>> votes)` with the multiply-then-divide pattern, consistent with the fix already applied to the VI-based reward algorithm elsewhere in the file, e.g.:
```java
reward += BigInteger.valueOf(userVote).multiply(BigInteger.valueOf(totalReward))
    .divide(BigInteger.valueOf(totalVote)).longValue();
```
This avoids any precision loss/rounding introduced by performing the division before the multiplication.

### Proof of Concept
Given `userVote = 1`, `totalVote = 3`, `totalReward = 10`:
- Correct (multiply-then-divide): `1 * 10 / 3 = 3` (integer truncation of the exact value `3.33...`).
- Current buggy order: `voteRate = (double)1/3 = 0.3333333333333333`; `reward += 0.3333333333333333 * 10 = 3.333333333333333` → cast to `long` via `+=` on a `long` accumulator truncates similarly here, but for values where `userVote/totalVote` cannot be represented exactly in a `double` (e.g., very large `totalVote`/`userVote` near `2^53`), the double-precision division loses bits before the multiplication ever occurs, whereas the integer multiply-then-divide preserves full precision until the final truncation — producing different (and for large-scale/long-running chains, cumulatively incorrect) reward totals versus the mathematically exact `userVote * totalReward / totalVote`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L89-134)
```java
  public void withdrawReward(byte[] address) {
    if (!dynamicPropertiesStore.allowChangeDelegation()) {
      return;
    }
    AccountCapsule accountCapsule = accountStore.get(address);
    long beginCycle = delegationStore.getBeginCycle(address);
    long endCycle = delegationStore.getEndCycle(address);
    long currentCycle = dynamicPropertiesStore.getCurrentCycleNumber();
    long reward = 0;
    if (beginCycle > currentCycle || accountCapsule == null) {
      return;
    }
    if (beginCycle == currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        return;
      }
    }
    //withdraw the latest cycle reward
    if (beginCycle + 1 == endCycle && beginCycle < currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        reward = computeReward(beginCycle, endCycle, account);
        adjustAllowance(address, reward);
        reward = 0;
        logger.info("Latest cycle reward {}, {}.", beginCycle, account.getVotesList());
      }
      beginCycle += 1;
    }
    //
    endCycle = currentCycle;
    if (CollectionUtils.isEmpty(accountCapsule.getVotesList())) {
      delegationStore.setBeginCycle(address, endCycle + 1);
      return;
    }
    if (beginCycle < endCycle) {
      reward += computeReward(beginCycle, endCycle, accountCapsule);
      adjustAllowance(address, reward);
    }
    delegationStore.setBeginCycle(address, endCycle);
    delegationStore.setEndCycle(address, endCycle + 1);
    delegationStore.setAccountVote(endCycle, address, accountCapsule);
    logger.info("Adjust {} allowance {}, now currentCycle {}, beginCycle {}, endCycle {}, "
            + "account vote {}.", Hex.toHexString(address), reward, currentCycle,
        beginCycle, endCycle, accountCapsule.getVotesList());
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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L260-269)
```java
  private long getOldReward(long begin, long end, List<Pair<byte[], Long>> votes) {
    if (dynamicPropertiesStore.allowOldRewardOpt()) {
      return rewardViCalService.getNewRewardAlgorithmReward(begin, end, votes);
    }
    long reward = 0;
    for (long cycle = begin; cycle < end; cycle++) {
      reward += computeReward(cycle, votes);
    }
    return reward;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L54-55)
```java
    mortgageService.withdrawReward(withdrawBalanceContract.getOwnerAddress()
        .toByteArray());
```
