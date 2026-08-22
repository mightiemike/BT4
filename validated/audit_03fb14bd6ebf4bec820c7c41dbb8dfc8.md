### Title
Unbounded per-cycle reward-computation loop in `MortgageService.getOldReward()` can cause consensus-level DoS - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
`MortgageService.withdrawReward()` and `MortgageService.queryReward()` compute a voter's reward by calling `computeReward(beginCycle, endCycle, accountCapsule)`, and when the old (pre-optimization) reward algorithm is active, that method falls back to `getOldReward()`, which iterates once per maintenance cycle between the account's last-claimed cycle and the current cycle: [1](#0-0) 
This is directly analogous to the Knox `_previewWithdraw()`/`_redeemMax()` bug class: an in-transaction loop whose iteration count is attacker/user-controlled and unbounded, with each iteration performing store reads and math, risking exhaustion of processing resources for a single transaction/block.

### Finding Description
`withdrawReward()` is invoked as part of the execution of ordinary, unprivileged, user-broadcast transactions — `VoteWitnessActuator.execute()`, `UnfreezeBalanceActuator.execute()`, `UnfreezeBalanceV2Actuator.execute()`, and `WithdrawBalanceActuator.execute()` — all reachable by any account submitting a normal signed transaction via the public broadcast-transaction RPC/HTTP endpoints. Inside `withdrawReward()`/`queryReward()`, the reward for the interval `[beginCycle, endCycle)` is computed via: [2](#0-1) 
When `beginCycle < newAlgorithmCycle`, the (potentially large) sub-range `[beginCycle, min(endCycle, newAlgorithmCycle))` is delegated to `getOldReward()`: [1](#0-0) 
`getOldReward()` executes a `for (long cycle = begin; cycle < end; cycle++)` loop that, for every single maintenance cycle in the range, calls `computeReward(cycle, votes)` which itself loops over every witness the account voted for and performs `DelegationStore` reads (`getReward`, `getWitnessVote`) plus floating-point math: [3](#0-2) 
The number of cycles (`end - begin`) is proportional to how long an account has gone without triggering a reward-consuming action (vote/freeze/unfreeze/withdraw). Since a vote can persist indefinitely (an account is not forced to periodically "claim"), and the account controls when to next call one of the four actuators above, the size of this loop is effectively unbounded and grows with elapsed maintenance cycles (each cycle is a fixed period, e.g. every few hours), not with any bounded on-chain data structure like `MAX_VOTE_NUMBER`.

### Impact Explanation
Because `getOldReward()`/`computeReward()` execute deterministically as part of block processing (every full node must re-execute the same actuator logic to validate/apply the block), an account that leaves an old, un-claimed vote in place across a very large number of cycles and then submits a single ordinary transaction (vote, freeze, unfreeze, or withdraw) forces every node in the network to perform an O(cycles × votes) computation with store reads for that single transaction. This can significantly slow down or stall block processing for the whole network (denial of service via a normal, unprivileged, broadcast transaction), and in the worst case could push transaction execution time past acceptable block-production windows, causing missed blocks or consensus stalls. This mirrors the Sherlock Knox H-2 impact category ("DoS" through unbounded loop cost) but here the loop cost is borne by every validating/full node rather than reverting a single transaction, making the practical severity potentially higher (network-wide processing delay vs. a single failed tx).

### Likelihood Explanation
Likelihood is moderate-to-low in practice because the codebase has already introduced `allowOldRewardOpt`/`RewardViCalService.getNewRewardAlgorithmReward()` as a mitigation path and a `newAlgorithmCycle` cutover, meaning that once the new reward algorithm parameter is switched on for all accounts (i.e., `beginCycle >= newAlgorithmCycle`), the legacy unbounded per-cycle loop is bypassed entirely: [4](#0-3) 
However, the vulnerable code path remains live for any account whose `beginCycle` is still below `newAlgorithmCycle` (i.e., accounts that voted before the optimization was enabled and have not yet interacted with a reward-triggering actuator since), and for any network/chain configuration where `allowOldRewardOpt` has not been enabled. This is a real, reachable condition triggered purely by unprivileged, ordinary broadcast transactions (`VoteWitnessContract`, `UnfreezeBalanceContract`, `UnfreezeBalanceV2Contract`, `WithdrawBalanceContract`), with no special privileges required.

### Recommendation
- Ensure `allowOldRewardOpt` (and the corresponding `RewardViCalService` optimized reward computation) is always enabled network-wide before any account can accumulate a large unclaimed cycle range, or force periodic reward settlement (e.g., cap the maximum number of cycles processed per call and carry over the remainder across multiple transactions) so a single transaction can never trigger unbounded iteration.
- Add a hard upper bound on `end - begin` cycles processed in a single `computeReward`/`getOldReward` invocation, similar to how `VoteWitnessActuator` bounds `MAX_VOTE_NUMBER`, and require reward computation to make forward progress across several transactions/blocks rather than doing it all atomically in the actuator that happens to be invoked first.
- Consider proactively migrating/settling all outstanding old-algorithm reward balances during a maintenance cycle (chain upgrade) rather than lazily on next user interaction, eliminating the unbounded-loop trigger entirely.

### Proof of Concept
1. Identify (or, in a test/private network, create) an account that voted for one or more witnesses while the legacy reward algorithm was in effect, and that has not since interacted with any of `VoteWitnessActuator`, `UnfreezeBalanceActuator`, `UnfreezeBalanceV2Actuator`, or `WithdrawBalanceActuator` for a large number of maintenance cycles (each cycle length is a fixed system parameter), so that `delegationStore.getBeginCycle(address)` remains far below `dynamicPropertiesStore.getCurrentCycleNumber()` and below `newAlgorithmCycle`.
2. Broadcast an ordinary, unprivileged transaction from that account invoking any of the above actuators (e.g., a small `UnfreezeBalanceContract` or `VoteWitnessContract`).
3. During execution, `MortgageService.withdrawReward()` → `computeReward(beginCycle, endCycle, accountCapsule)` → `getOldReward()` runs the `for (long cycle = begin; cycle < end; cycle++)` loop once per cycle in the (large) range, each iteration invoking `computeReward(cycle, votes)` which reads `DelegationStore` per voted witness — reproducing the same class of resource-exhaustion-through-unbounded-loop behavior identified in the Knox report, but executed deterministically by every full node processing the block rather than only reverting the submitter's own transaction.

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
