### Title
Witness reward is permanently lost when a cycle's recorded vote count is zero - (File: `chainbase/src/main/java/org/tron/core/service/RewardViCalService.java`, `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
The reported Blueberry bug is that a reward accumulator (`rewardPerTokenStored`) is advanced/dropped for a period whenever `totalSupply == 0`, permanently losing the rewards that should have accrued in that window. Java-tron's DPoS reward-vi (voter-index) accounting has the same root-cause shape: block/standby/tx-fee rewards are credited to a witness for a cycle unconditionally, but the distribution of that reward to voters is skipped entirely whenever the recorded vote count for that witness/cycle is `0`, and the skipped amount is never re-queued or refunded — it is silently orphaned in the DB forever.

### Finding Description
Rewards for a super representative (SR) are accrued per-cycle regardless of vote count. `MortgageService.payReward()` is called unconditionally for every produced block and every standby-witness payout, storing the (post-brokerage) reward via `delegationStore.addReward(cycle, witnessAddress, value)`: [1](#0-0) 

That reward is later converted into a per-vote "Vi" delta so voters can withdraw their proportional share. Both the streaming calculator, `RewardViCalService.accumulateWitnessVi`, and the legacy path (`DelegationStore.accumulateWitnessVi`) use the exact same guard: if the recorded vote count for that cycle is `0` (or the reward is `0`), the delta is not accumulated — the previous Vi value is simply carried forward, effectively discarding the reward that was already credited to the cycle: [2](#0-1) [3](#0-2) 

The old-algorithm reward computation (`MortgageService.computeReward(cycle, votes)`) has the identical guard — if `totalVote == 0` for that cycle, the loop `continue`s and that cycle's stored reward is never paid to anyone: [4](#0-3) 

The value stored via `delegationStore.addReward` for that cycle is only ever consumed through these two code paths, both of which drop it when `voteCount == 0`. There is no mechanism to carry the orphaned reward forward into a future cycle where votes exist, nor to refund it. This is structurally identical to the reported bug: reward accrual (`addReward` — analogous to reward emission over elapsed time) is decoupled from the presence of any "depositors" (`voteCount` — analogous to `totalSupply`), and whenever the latter is zero at accrual time, the reward is permanently and silently lost.

A zero-vote cycle for a currently scheduled top-27 SR is reachable by ordinary users: the witness list/vote snapshot used to schedule block production for a cycle is fixed for the cycle, but voters can withdraw or move votes at any time via `VoteWitnessProcessor`/`WithdrawBalance`/unfreeze actions during that same cycle, driving the *recorded* `getWitnessVote(cycle, srAddress)` value for that cycle toward `0` for that SR while it (or its transaction-fee share) still continues to receive `payBlockReward`/`payTransactionFeeReward` credits for blocks it produces before the reassignment takes effect.

### Impact Explanation
Reward funds credited to a witness for a given cycle become permanently unclaimable/orphaned in the `delegation` store the moment that cycle's recorded vote total is `0`, with no path to reclaim or redistribute them. This is a real, non-recoverable value-loss/accounting bug affecting the reward pool that funds voter payouts — the same fund-loss class as the referenced report (rewards computed but never deliverable to any party).

### Likelihood Explanation
Likelihood is moderate/low: it requires a specific timing condition — a scheduled SR whose recorded per-cycle vote count drops to zero while it is still producing blocks/receiving fee-pool splits in that cycle (e.g., mass unvoting, unfreeze cascades, or a witness that briefly enters the schedule with negligible/zero votes recorded for a cycle). It does not require any privileged role and is triggerable purely through normal voting/unfreezing actions, but the exact zero-vote-with-active-reward window is a narrow, non-attacker-controlled edge case rather than something directly and cheaply forced on demand.

### Recommendation
When accumulating the per-cycle Vi delta (or the legacy per-cycle reward), do not silently discard rewards recorded for a cycle whose vote total is zero. Instead, either (a) roll the un-distributable reward forward and add it to the next cycle that has a non-zero vote total for that witness before computing the delta, or (b) return it to the general transaction-fee/witness reward pool so it is not permanently lost. This mirrors the recommended fix in the referenced report: never advance the accounting checkpoint (cycle/Vi) in a way that discards value tied to a zero-supply (zero-vote) period.

### Proof of Concept
1. SR `W` is scheduled as an active witness for cycle `C` (top-27 schedule fixed at the start of `C`).
2. During cycle `C`, all accounts holding votes for `W` unvote/unfreeze, driving `delegationStore.getWitnessVote(C, W)` toward `0` for that cycle (set at cycle rollover per `DelegationStore.setWitnessVote`).
3. `W` still produces one or more blocks during cycle `C` and/or shares in the transaction fee pool, so `MortgageService.payBlockReward`/`payTransactionFeeReward` → `payReward` calls `delegationStore.addReward(C, W, value)`, storing a non-zero reward for cycle `C`.
4. When `accumulateWitnessVi`/`DelegationStore.accumulateWitnessVi` processes cycle `C` for `W`, it observes `voteCount == 0` and forwards the previous Vi unchanged — the reward added in step 3 is never converted into a deliverable delta: [2](#0-1) 
5. Any voter later withdrawing rewards spanning cycle `C` via `MortgageService.withdrawReward`/`queryReward` will never receive the value stored in step 3, because `computeReward` only reads Vi deltas (or, on the legacy path, explicitly `continue`s when `totalVote == 0`): [5](#0-4) 
6. The reward value added in step 3 remains permanently stored under the cycle/address "reward" key with no consumer — it is lost.

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
