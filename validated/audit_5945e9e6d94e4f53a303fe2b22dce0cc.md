This confirms the analog: `MaintenanceManager.doMaintenance()` calls `delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount())` for every witness (active or not) at line 99, and a witness's `voteCount` can legitimately be `0` (e.g., a newly-created/never-voted-for Super Representative or a witness that received votes but whose delegators later withdrew to zero before the next maintenance cycle, or a witness in the standby/128 list). Block rewards and transaction-fee rewards continue to be paid to that witness's pool via `MortgageService.payReward()` → `delegationStore.addReward(cycle, witnessAddress, value)`, but when `accumulateWitnessVi` runs with `voteCount == 0`, the reward is silently dropped rather than accumulated into the VI (reward-per-vote index). [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Witness rewards are permanently lost when a witness's vote count is zero at maintenance time (VI accumulation drops the cycle's reward) - (File: chainbase/src/main/java/org/tron/core/store/DelegationStore.java)

### Summary
The DPoS reward-per-vote (VI, "value index") accounting mechanism silently discards a witness's accumulated block/transaction-fee rewards for a cycle whenever that witness's total vote count is `0` at the time `accumulateWitnessVi` runs. This is the same root-cause bug class as the reported `StakingRewards.rewardPerToken()` issue: reward accrual continues (blocks are produced, `addReward` is called) while the denominator used to distribute the reward (`totalSupply` in the report / `voteCount` here) is zero, so the reward becomes permanently unclaimable rather than being carried forward or refunded.

### Finding Description
`MaintenanceManager.doMaintenance()` iterates over all witnesses every maintenance cycle and unconditionally calls `delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount())` [2](#0-1) .

Inside `DelegationStore.accumulateWitnessVi`, if `reward == 0 || voteCount == 0`, the method just forwards the previous VI value unchanged instead of adding a delta:
```java
public void accumulateWitnessVi(long cycle, byte[] address, long voteCount) {
    BigInteger preVi = getWitnessVi(cycle - 1, address);
    long reward = getReward(cycle, address);
    if (reward == 0 || voteCount == 0) { // Just forward pre vi
      if (!BigInteger.ZERO.equals(preVi)) {
        setWitnessVi(cycle, address, preVi);
      }
    } else { // Accumulate delta vi
      ...
    }
}
``` [1](#0-0) 

However, `getReward(cycle, address)` can legitimately be non-zero for that same cycle: block rewards and transaction fee rewards are credited into the same per-cycle bucket via `MortgageService.payReward()`, which is invoked from `payBlockReward`/`payTransactionFeeReward` for every block produced, independent of the witness's current vote total:
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
``` [3](#0-2) 

A witness can have `voteCount == 0` while still producing blocks (e.g., a witness whose delegators fully withdraw votes mid-cycle before the vote total is re-synced by `MaintenanceManager.doMaintenance()`, or edge cases around cycle bootstrapping/witness churn where `witnessCapsule.getVoteCount()` is `0` at the moment `accumulateWitnessVi` runs for that cycle). When this happens, the non-brokerage portion of the reward that was added via `addReward` for that cycle is never reflected as a VI delta — it is simply forwarded/dropped, so no voter's `computeReward` (which derives reward strictly from `deltaVi * userVote`, see `MortgageService.computeReward` lines 215-227) can ever recover it. The same drop pattern also exists in the parallel implementations `RewardViCalService.accumulateWitnessVi` (used for historical VI backfill) and the old-algorithm path `MortgageService.computeReward(cycle, votes)` which `continue`s (skips) when `totalVote == 0` [4](#0-3) , meaning the reward previously stored via `addReward` for that witness/cycle is never paid to anyone.

### Impact Explanation
This causes a permanent loss of TRX allocated as block/transaction-fee rewards: once a cycle's reward is dropped by `accumulateWitnessVi` (or skipped in the old-algorithm `computeReward`), it can never be claimed by any account, matching the "Medium" severity accounting-corruption class in the reference report — funds are minted/allocated by the protocol's reward mechanism but become permanently stuck/unclaimable, silently shrinking the effective circulating incentive without any corresponding account credit.

### Likelihood Explanation
This does not require a privileged actor or malicious peer — it is a naturally reachable state transition inside the standard DPoS reward accounting path that runs on every maintenance cycle for every witness, triggered purely by normal vote withdrawal/reallocation activity from anonymous accounts broadcasting vote transactions. Any witness whose vote count transitions to zero within a cycle boundary (fully unvoted, e.g. all backers withdraw/re-delegate) while it (or its bucket) still has an accrued but not-yet-VI-applied reward will trigger this loss deterministically.

### Recommendation
When `accumulateWitnessVi` (and the corresponding old-algorithm `computeReward`) encounters `voteCount == 0` but `reward > 0`, do not silently drop the reward. Instead, carry the un-distributable reward forward into the next cycle's reward bucket for the same witness (e.g., `delegationStore.addReward(cycle + 1, address, reward)`) so it becomes distributable once the witness has voters again, or route it to a protocol-controlled reclaim mechanism, rather than leaving it permanently orphaned in a cycle bucket that will never again be read by any voter's VI delta calculation.

### Proof of Concept
1. A witness `W` receives block rewards for cycle `N` via `payBlockReward`/`payTransactionFeeReward` → `MortgageService.payReward` → `delegationStore.addReward(N, W, value)`, so `getReward(N, W) > 0`.
2. Before `MaintenanceManager.doMaintenance()` runs for cycle `N`'s VI accumulation step, all accounts that had voted for `W` withdraw/reallocate their votes (via standard `VoteWitnessActuator`/`WithdrawBalanceActuator` flows), so `witnessCapsule.getVoteCount()` becomes `0` by the time `consensusDelegate.getAllWitnesses().forEach(...)` executes.
3. `delegationStore.accumulateWitnessVi(N, W, 0)` is called; since `voteCount == 0`, the branch that would compute `deltaVi = reward * DECIMAL / voteCount` is skipped, and the previous VI is simply carried forward unchanged.
4. No account's `MortgageService.computeReward` (which only derives reward from `endVi - beginVi` multiplied by the voter's vote count) can ever recover the reward added in step 1, because the VI for cycle `N` never reflects it — the reward is permanently orphaned in the `N-W-reward` key with no code path reading it again for distribution.

### Citations

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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L96-101)
```java
    if (dynamicPropertiesStore.useNewRewardAlgorithm()) {
      long curCycle = dynamicPropertiesStore.getCurrentCycleNumber();
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount());
      });
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
