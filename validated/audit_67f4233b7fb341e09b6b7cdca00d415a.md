## Analog Found

### Title
Witness/voter rewards recorded via `delegationStore.addReward` are silently discarded when a witness has zero total votes for the cycle, permanently stranding the reward with no recipient - ([File: chainbase/src/main/java/org/tron/core/store/DelegationStore.java])

### Summary
This is analogous to the reported bug: `MortgageService::payBlockReward` / `payTransactionFeeReward` / `payStandbyWitness` record a witness's per-cycle reward pool via `DelegationStore.addReward` [1](#0-0) , expecting that pool to later be distributed to voters proportionally through the vote-index (`Vi`) mechanism. But the code that folds that reward into the `Vi` accumulator — `DelegationStore.accumulateWitnessVi` and its duplicate in `RewardViCalService` — discards the reward entirely whenever the recorded vote count for that witness/cycle is zero, instead of checking whether there is any vote "supply" to distribute the interest/reward to.

### Finding Description
`DelegationStore.accumulateWitnessVi` is: [2](#0-1) 

If `voteCount == 0` for the cycle (even though `reward` for that witness/cycle is non-zero, because reward accrual and vote-count snapshotting are two independently mutated pieces of state), the code takes the "just forward pre vi" branch, meaning the reward that was already booked via `addReward` in that cycle is never converted into a `Vi` delta. Since `Vi` is the only mechanism `MortgageService.computeReward`/`VoteRewardUtil.computeReward` use to compute a voter's share of reward [3](#0-2) , that reward amount becomes permanently unattributable to any account — exactly like the reported bond-interest scenario where interest is withdrawn/booked but there is no bond holder to receive it.

The same pattern is duplicated verbatim in `RewardViCalService.accumulateWitnessVi`, used for the historical Merkle-root reconciliation of the new reward algorithm: [4](#0-3) 

The production call site is `MaintenanceManager.doMaintenance()`, invoked once per maintenance cycle for every witness: [5](#0-4) 

This runs for the reward system used once `useNewRewardAlgorithm()`/`allowChangeDelegation()` is active (the default reward-distribution path on current chains), meaning the flaw is on the live path, not a legacy/disabled branch.

### Impact Explanation
Whenever a witness accumulates a nonzero reward for a cycle (from block rewards, transaction-fee-pool rewards, or standby-witness pay recorded through `MortgageService.payReward`) while its total recorded vote count for that cycle is `0`, that reward's voter-share is silently and permanently lost — it is neither credited to the witness's own allowance, nor to any voter, nor rolled forward for later recovery. There is no error, no revert, and no way for the affected voters (who may have voted moments before/after the snapshot boundary) to claim it. This is a real accounting/state-loss defect: minted/allocated reward becomes unclaimable, matching the "stuck interest with no recipient" impact class from the source report.

### Likelihood Explanation
The precondition — a witness with `reward > 0` and `voteCount == 0` for the same cycle — is not a common majority-case, but is reachable without any privileged role: it requires only ordinary `VoteWitnessContract`/`UnfreezeBalanceV2` activity by voters causing a witness's vote count to fall to zero while its previously-earned/queued cycle reward (booked via unconditional `addReward` calls tied to block production/fee-pool distribution) is still pending conversion into `Vi`. No special permissions are needed to trigger the underlying vote/unfreeze transactions.

### Recommendation
In `DelegationStore.accumulateWitnessVi` (and the duplicated logic in `RewardViCalService.accumulateWitnessVi`), when `voteCount == 0` but `reward > 0`, do not silently drop the reward. Instead, either roll the un-distributable reward forward to a subsequent cycle/witness-level allowance, redirect it to the witness's own allowance, or credit it back to the transaction-fee pool / block-reward pool so the newly-minted TRX is not permanently orphaned.

### Proof of Concept
1. A witness `W` receives votes and becomes active; during cycle `N` it produces blocks, so `MortgageService.payBlockReward`/`payTransactionFeeReward` call `delegationStore.addReward(N, W, value)`, booking a nonzero reward pool for cycle `N`.
2. Before the maintenance cycle boundary finalizes vote counts for cycle `N`, all voters for `W` fully unvote/unfreeze, driving `W`'s recorded vote count for cycle `N` to `0`.
3. At the next `MaintenanceManager.doMaintenance()` call, `delegationStore.accumulateWitnessVi(N, W, 0)` is invoked; since `voteCount == 0`, the branch `if (reward == 0 || voteCount == 0)` simply forwards the previous `Vi` without folding in the reward recorded in step 1.
4. No voter (former or new) can ever claim that cycle's reward via `MortgageService.withdrawReward`/`queryReward`, since those only compute rewards from `Vi` deltas — the reward value is permanently stranded.

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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L96-101)
```java
    if (dynamicPropertiesStore.useNewRewardAlgorithm()) {
      long curCycle = dynamicPropertiesStore.getCurrentCycleNumber();
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }
```
