### Title
Silent truncating `BigInteger.longValue()` cast in vote-reward computation can corrupt reward accounting - ([File: chainbase/src/main/java/org/tron/core/service/MortgageService.java])

### Summary
The vote-reward computation logic converts an unbounded `BigInteger` (accumulated "virtual index" delta multiplied by a user's vote count) into a `long` using the truncating `BigInteger.longValue()` method instead of a range-checked conversion such as `longValueExact()`. This is the same bug class as the PoolTogether `_getVaultPortion()` finding: an unsafe/truncating numeric downcast on a value that legitimately can exceed the target type's range, causing a silent, unnoticed corruption of the computed value rather than a revert.

### Finding Description
Witness vote rewards accrue over cycles as a monotonically-growing `BigInteger` "virtual index" (`vi`) with no bound, via `DelegationStore.accumulateWitnessVi`: [1](#0-0) 

`DECIMAL_OF_VI_REWARD` is a fixed `10^18` scaling constant: [2](#0-1) 

When a user withdraws/queries their reward, the code computes `deltaVi = endVi - beginVi`, multiplies it by the user's `long` vote count, divides by the `10^18` scale, and then calls `.longValue()` to obtain the final `long` reward — in three separate, independently-reachable code paths:

- `MortgageService.computeReward` (legacy/native reward path used by `withdrawReward`/vote flows): [3](#0-2) 

- `VoteRewardUtil.computeReward` (TVM-exposed native contract path, reachable by any contract/account calling the `WithdrawReward`/vote-related precompiles): [4](#0-3) 

- `RewardViCalService.getNewRewardAlgorithmReward`: [5](#0-4) 

`BigInteger.longValue()` does **not** check bounds — if the multiplied/divided intermediate value does not fit in a signed 64-bit `long`, it silently returns the low-order 64 bits (which can wrap to an arbitrary or even negative value) instead of throwing. This is functionally identical to the reported `_getVaultPortion()` issue where `uint256`→`int256` casts silently overflow instead of reverting.

Because `deltaVi` accumulates additively every cycle for witnesses that receive nonzero reward while carrying a small `voteCount` denominator (`accumulateWitnessVi` divides `reward * 10^18` by `voteCount`), `vi` (and thus `deltaVi` over a long withdrawal interval) can grow to very large magnitudes over many cycles. When later multiplied by a legitimate user's `voteCount` and divided by `10^18`, the result can exceed `Long.MAX_VALUE` (~9.22e18), triggering the silent truncation.

Notably, the codebase elsewhere demonstrates awareness of this exact class of bug and uses the safe alternative: `ResourceProcessor.calculateGlobalLimitV2` explicitly uses `longValueExact()` to force a hard failure on overflow instead of silent truncation: [6](#0-5) 

This shows the reward-computation call sites are inconsistent with the project's own established safe-casting pattern.

### Impact Explanation
A silent truncation in reward computation directly corrupts on-chain account accounting: `MortgageService.adjustAllowance` / `VoteRewardUtil.adjustAllowance` credit the (possibly wrapped/negative/garbage) truncated value to a user's `allowance`, which is later paid out as real TRX balance via `WithdrawRewardProcessor.execute`. This can cause users to receive drastically wrong (arbitrarily smaller, zero, or corrupted) reward payouts compared to what the correct accumulated `vi`-based computation would yield — a direct accounting/settlement divergence, matching the "rewards to disperse and be lower" impact described in the referenced report.

### Likelihood Explanation
The reward path is reachable by any unprivileged account that votes for a witness/SR and later calls the withdraw-reward flow (both the legacy native path and the TVM precompile path). The overflow condition requires `deltaVi * userVote` to exceed `Long.MAX_VALUE` after the `10^18` division, which becomes increasingly likely the longer a user delays withdrawing rewards while voting for witnesses with historically small `voteCount` denominators (which magnify `vi` growth), or through witnesses accumulating rewards over very long uptime. This is a data/state-dependent condition rather than a one-transaction exploit, making likelihood moderate rather than trivial, but it is a real, unprivileged, non-mocked, reachable path in production reward accounting.

### Recommendation
Replace the truncating `BigInteger.longValue()` calls in `MortgageService.computeReward`, `VoteRewardUtil.computeReward`, and `RewardViCalService.getNewRewardAlgorithmReward` with `longValueExact()` (as already done in `ResourceProcessor.calculateGlobalLimitV2`), so that an out-of-range conversion throws `ArithmeticException` and is handled explicitly (e.g., reverted/capped) rather than silently corrupting reward accounting.

### Proof of Concept
1. A witness SR receives a nonzero block/tx fee reward for a cycle while its total `voteCount` recorded in `DelegationStore` is very small (e.g. `1`), causing `accumulateWitnessVi` to add a very large `deltaVi = reward * 10^18 / 1` to the running `vi` value for that cycle: [7](#0-6) .
2. This repeats over many cycles, so the stored `vi` (a `BigInteger`, unbounded) keeps growing without limit.
3. A legitimate voter with a large `voteCount` who has not withdrawn for a long span of cycles calls `withdrawReward`/`queryReward`. `computeReward` computes `deltaVi = endVi - beginVi` (large), multiplies by the voter's `userVote` (large), divides by `10^18`, and the result exceeds `Long.MAX_VALUE`: [8](#0-7) .
4. `.longValue()` silently truncates this to an arbitrary 64-bit value instead of throwing, and this corrupted value is credited via `adjustAllowance` and eventually paid out through `WithdrawRewardProcessor.execute`, producing an incorrect on-chain balance change.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L20-22)
```java
  public static final long REMARK = -1L;
  public static final int DEFAULT_BROKERAGE = 20;
  public static final BigInteger DECIMAL_OF_VI_REWARD = BigInteger.valueOf(10).pow(18);
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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L215-228)
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

**File:** chainbase/src/main/java/org/tron/core/service/RewardViCalService.java (L143-171)
```java
  public long getNewRewardAlgorithmReward(long beginCycle, long endCycle,
                                          List<Pair<byte[], Long>> votes) {
    if (!isDone()) {
      logger.warn("rewardViCalService is not done, wait for it");
      try {
        lock.await();
      } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
        throw new TronDBException(e);
      }
    }

    long reward = 0;
    if (beginCycle < endCycle) {
      for (Pair<byte[], Long> vote : votes) {
        byte[] srAddress = vote.getKey();
        BigInteger beginVi = getWitnessVi(beginCycle - 1, srAddress);
        BigInteger endVi = getWitnessVi(endCycle - 1, srAddress);
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

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L371-378)
```java
  protected long calculateGlobalLimitV2(long frozeBalance,
      long totalLimit, long totalWeight) {
    return BigInteger.valueOf(frozeBalance)
        .multiply(BigInteger.valueOf(totalLimit))
        .divide(BigInteger.valueOf(TRX_PRECISION)
            .multiply(BigInteger.valueOf(totalWeight)))
        .longValueExact();
  }
```
