## Title
Reward index (`Vi`) is advanced without accumulating pending rewards when a witness's vote count is zero, permanently orphaning that cycle's block/standby reward - (File: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java`)

### Summary
`DelegationStore.accumulateWitnessVi()` is the java-tron analog of Curve's `LiquidityGauge` reward-per-share accounting. Just like the reported bug updates `last_update` even when `totalSupply == 0` (causing deposited rewards to become unclaimable), `accumulateWitnessVi()` advances the witness's reward index (`Vi`) for the cycle even when the reward pot for that witness in that cycle is non-zero but the "total shares" analog (`voteCount`) is zero — instead of accumulating the reward into the index, it simply forwards the previous index and the reward silently becomes unclaimable forever.

### Finding Description
`accumulateWitnessVi` is invoked once per maintenance cycle for every witness: [1](#0-0) 

Its implementation is: [2](#0-1) 

The `Vi` index (analogous to Curve's `integrate_inv_supply`/reward-per-share accumulator) is the mechanism by which voters later recompute their share of a witness's reward for a given cycle in `MortgageService.computeReward()`: [3](#0-2) 

If `reward > 0` (block reward / tx-fee reward / standby reward was paid into that witness's cycle bucket via `DelegationStore.addReward`) but `voteCount == 0` (the "total shares" for that cycle, analogous to Curve's `totalSupply == 0`), the delta is **not** folded into `Vi` — the code takes the "just forward pre Vi" branch, exactly like Curve's `last_update` being advanced while the rewards remain undistributed. Because `Vi` for that cycle is left unchanged (equal to the previous cycle's `Vi`), the `deltaVi` computed later by any voter for that cycle window is always `0`, so the reward amount stored via `addReward()` for that (cycle, witness) key is never reflected in any voter's `computeReward()` result. The stored reward value itself is orphaned in the DB with no path to be paid out — money reserved from the emission schedule is permanently stuck, matching the reported "blocked rewards on the contract" impact.

The state that drives `voteCount` (`witness.getVoteCount()`) is the vote tally established as of the *previous* maintenance boundary, held fixed while the current cycle's blocks are produced. A witness can end a cycle with `voteCount == 0` (e.g., all delegators fully un-voted via `VoteWitnessActuator`/`VoteWitnessProcessor` right before/at a maintenance boundary) while it still received `payBlockReward`/`payStandbyWitness`/`payTransactionFeeReward` credits earlier in that same cycle: [4](#0-3) [5](#0-4) 

Reaching this state requires only unprivileged, ordinary vote/unvote transactions (`VoteWitnessContract`, or via TVM `voteWitness`), no special privileges.

### Impact Explanation
Once a witness's reward bucket for a given cycle is orphaned this way, the reward TRX that was already deducted from the emission budget (`getWitnessPayPerBlock`, `getWitness127PayPerBlock`, transaction fee pool) can never be claimed by any voter through `MortgageService.withdrawReward`/`queryReward` or the TVM equivalent (`WithdrawRewardProcessor`/`VoteRewardUtil`). This is a reward/resource-accounting corruption: funds are accounted as distributed (deducted from the pool) but become permanently unclaimable, silently reducing the effective reward pool available to voters over time.

### Likelihood Explanation
Triggering the condition requires only that voters fully withdraw their votes from a witness right at a cycle boundary while that witness still produced blocks / received standby pay during the ending cycle — a state reachable purely through normal `VoteWitnessContract` broadcast transactions, with no elevated privileges. Because the new reward algorithm (`useNewRewardAlgorithm`) runs this accumulation every maintenance cycle for every witness, the window for this occurring is not contrived; it is a natural consequence of allowed vote changes near cycle boundaries.

### Recommendation
In `DelegationStore.accumulateWitnessVi`, do not silently drop the reward when `voteCount == 0` and `reward > 0`. Either (a) roll the un-distributable reward forward into the next cycle's reward bucket for the same witness (`addReward(cycle + 1, address, reward)`) before forwarding `Vi`, or (b) only advance the "last processed" cycle marker without treating the reward as consumed, so it can be redistributed once the witness has non-zero votes again — mirroring the fix suggested for `LiquidityGauge` (only update the reward/last-update state when the "total supply" is non-zero, otherwise preserve the reward for later distribution).

### Proof of Concept
1. Witness `W` has non-zero votes and is active during cycle `N`; it earns block rewards via `MortgageService.payBlockReward` → `DelegationStore.addReward(N, W, value)`.
2. Before/at the maintenance boundary ending cycle `N`, all voters of `W` broadcast `VoteWitnessContract` transactions removing their votes, so by the time `MaintenanceManager.doMaintenance()` runs, `witness.getVoteCount()` for `W` is `0`.
3. `doMaintenance()` calls `delegationStore.accumulateWitnessVi(N, W, 0)`. Since `voteCount == 0`, the branch at `DelegationStore.java:136-139` executes, forwarding `Vi(N) = Vi(N-1)` without incorporating the reward added in step 1.
4. Any subsequent computation of a voter's reward for cycle `N` via `MortgageService.computeReward` uses `deltaVi = Vi(endCycle-1) - Vi(beginCycle-1)`, which does not include the cycle-`N` reward, so the reward added in step 1 can never be withdrawn by anyone — it is permanently stuck.

### Citations

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L96-101)
```java
    if (dynamicPropertiesStore.useNewRewardAlgorithm()) {
      long curCycle = dynamicPropertiesStore.getCurrentCycleNumber();
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount());
      });
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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1946-1966)
```java
  private void payReward(BlockCapsule block) {
    WitnessCapsule witnessCapsule =
        chainBaseManager.getWitnessStore().getUnchecked(block.getInstance().getBlockHeader()
            .getRawData().getWitnessAddress().toByteArray());
    if (getDynamicPropertiesStore().allowChangeDelegation()) {
      mortgageService.payBlockReward(witnessCapsule.getAddress().toByteArray(),
          getDynamicPropertiesStore().getWitnessPayPerBlock());
      mortgageService.payStandbyWitness();

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
    } else {
```
