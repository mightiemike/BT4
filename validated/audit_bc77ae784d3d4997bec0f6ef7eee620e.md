### Title
Unbounded per-cycle loop DoS in `MortgageService.withdrawReward`/`queryReward` legacy reward path - (`chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
`MortgageService` computes staking rewards for an address by iterating cycle-by-cycle from `beginCycle` to `endCycle`, with a nested loop over the account's vote list, whenever the "old" (pre-VI) reward algorithm path is taken. This mirrors exactly the AI Arena `MergingPool::claimRewards`/`RankedBattle::claimNRN` bug class: a loop bounded by "number of periods since the account last interacted," which grows unboundedly the longer a user goes without calling withdraw/vote/unfreeze, eventually exceeding available gas/CPU for a single transaction.

### Finding Description
`MortgageService.withdrawReward` and `MortgageService.queryReward` compute the reward owed to an address between `beginCycle` and `endCycle` via `computeReward(long beginCycle, long endCycle, AccountCapsule accountCapsule)`: [1](#0-0) 

which, for cycles preceding the VI (value-index) algorithm activation, falls back to `getOldReward`: [2](#0-1) 

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

When `allowOldRewardOpt` is not enabled, this executes an outer loop over every cycle between `begin` and `end` (potentially tens of thousands, one per maintenance period, e.g. every ~6 hours since genesis), with an inner loop over the account's full vote list (`computeReward(long cycle, List<Pair<byte[], Long>> votes)`): [3](#0-2) 

This is structurally identical to the reported `MergingPool::claimRewards`/`RankedBattle::claimNRN` bug: an outer loop bounded by "rounds/cycles since last claim" nested with an inner loop over per-round/per-cycle data, causing gas/CPU cost to grow linearly (or worse) with elapsed time.

This path is reachable from ordinary, unprivileged user transactions. `withdrawReward` is invoked directly from:
- `WithdrawBalanceActuator.execute` (`WithdrawBalanceContract`) [4](#0-3) 
- `UnfreezeBalanceActuator.execute` (`UnfreezeBalanceContract`) [5](#0-4) 
- `UnfreezeBalanceV2Actuator.execute` (`UnfreezeBalanceV2Contract`) [6](#0-5) 

all of which are ordinary broadcast transactions any account can submit against itself.

The project's own mitigation for this is `RewardViCalService`, which precomputes VI deltas so that reward computation becomes O(votes) instead of O(cycles × votes) once `allowOldRewardOpt` is turned on: [7](#0-6) . This confirms the team recognized the same class of unbounded-loop issue and added an opt-in fix — but the vulnerable code path (`getOldReward`'s per-cycle loop) remains in the code and is exercised whenever `allowOldRewardOpt` is not enabled for the relevant account/cycle range.

### Impact Explanation
An account (witness voter) that accumulates a very large `endCycle - beginCycle` gap without withdrawing rewards, unfreezing, or re-voting, and whose reward computation falls into the `getOldReward` per-cycle loop (i.e., before `allowOldRewardOpt`/VI backfill is active for that range), can have its `withdrawReward`/`queryReward` computation cost grow proportionally to the number of elapsed cycles. In the worst case this can make the triggering transaction (`WithdrawBalanceContract`, `UnfreezeBalanceContract`, `UnfreezeBalanceV2Contract`) exceed the block/transaction resource limits, permanently preventing that account from withdrawing its earned rewards or unfreezing/reallocating its stake — a denial of service against the account's own funds, directly analogous to the confirmed AI Arena finding.

### Likelihood Explanation
Exploitability depends on network state: it requires the account's unwithdrawn cycle range to predate/exceed the point where `allowOldRewardOpt` (or the VI backfill in `RewardViCalService`) has been activated for that address. Since `allowOldRewardOpt` is a committee-controlled chain parameter and the VI backfill runs once at node startup for historical cycles, in a mature, correctly configured network this path is largely neutralized once the flag is enabled and backfill completes. However, the vulnerable branch is still present in code and would be live during any period where the flag is disabled or where a new/rolled-back chain has not yet completed the VI backfill, making this a real but state-dependent DoS risk rather than a constantly-exploitable one.

### Recommendation
- Remove or hard-cap the fallback branch in `getOldReward` so it can never execute an unbounded per-cycle loop; always route through the VI-based O(1)-per-vote computation (as `RewardViCalService` already does), or enforce a maximum number of cycles processed per call with a resumable/incremental claim mechanism (matching the `totalRoundsToConsider` pattern from the AI Arena mitigation).
- If a bounded fallback is required for safety/compatibility, add an explicit cap on `(end - begin)` and require multiple transactions to fully catch up, rather than allowing a single call to process the entire backlog.
- Ensure `allowOldRewardOpt` (or equivalent) is enabled by default/at genesis for any new network, so the unbounded per-cycle branch is never reachable in practice.

### Proof of Concept
Conceptual reproduction (cannot run against production chain without control over `allowOldRewardOpt`):
1. On a network/config where `allowOldRewardOpt` is disabled (or for an account whose `beginCycle` predates the VI backfill low-water mark), have an account vote for `MAX_VOTE_NUMBER` witnesses and then not withdraw for N maintenance cycles (N large, e.g. tens of thousands).
2. Call `WithdrawBalanceContract` (or `UnfreezeBalanceContract`) for that account.
3. Observe that `MortgageService.withdrawReward` → `computeReward` → `getOldReward` executes an O(N × votes) loop, with per-transaction cost growing linearly in N until it exceeds the node's execution/time or the transaction's resource budget, mirroring the `testClaimRewardsDOS`/`testClaimNRNDoS` gas-growth pattern from the referenced AI Arena report.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L73-74)
```java
    //
    mortgageService.withdrawReward(ownerAddress);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L71-72)
```java

    mortgageService.withdrawReward(ownerAddress);
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
