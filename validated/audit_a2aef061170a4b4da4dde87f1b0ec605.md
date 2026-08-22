### Title
Witness rewards become permanently unclaimable when `voteCount == 0` in `DelegationStore.accumulateWitnessVi` / `MortgageService.computeReward` - ([File: chainbase/src/main/java/org/tron/core/store/DelegationStore.java])

### Summary
When a Super Representative (SR) is credited with block/transaction-fee rewards for a cycle in which its total vote count is `0`, the reward is stored via `DelegationStore.addReward()` but is never folded into the `Vi` reward-index or distributed to any voter, because the accumulation and payout logic explicitly skips when `voteCount == 0`. The credited reward amount (which, for the transaction-fee case, is deducted from a real, finite `TransactionFeePool`) becomes permanently unclaimable by any account — functionally identical to the reported "ETH stuck when `totalSupply == 0`" bug class.

### Finding Description
Every block, `Manager.payReward()` calls `MortgageService.payBlockReward()` / `payTransactionFeeReward()` unconditionally for the block-producing witness: [1](#0-0) 

These funnel into `payReward()`, which unconditionally calls `delegationStore.addReward(cycle, witnessAddress, value)` for the *remaining* (non-brokerage) portion of the reward, with no check on whether the witness actually has any votes recorded for that cycle: [2](#0-1) 

Later, `MaintenanceManager.doMaintenance()` accumulates this stored reward into a per-witness reward index (`Vi`) via `DelegationStore.accumulateWitnessVi`, which is the mechanism voters later use to compute their share of reward: [3](#0-2) 

Crucially, if the witness's vote count for that cycle is `0` (e.g., all voters unfroze/withdrew their votes, or the SR became active with no votes recorded that cycle), the accumulated reward is silently dropped — the `Vi` (reward index) is simply forwarded from the previous cycle instead of being incremented by the reward: [4](#0-3) 

This mirrors `EsEMBR.sol`'s `receive()`/`totalEthPerEsembr` bug precisely: money is received/credited into the accounting system (`addReward`), but the state variable that governs its later distribution (`Vi`) is left unmodified when the divisor (`voteCount`, analogous to `totalSupply`) is zero, so the reward becomes stuck — no voter can ever claim it, and there's no path to reclaim or roll it back to any pool. The same skip condition is duplicated in the old-algorithm code path via `totalVote == DelegationStore.REMARK || totalVote == 0` in `MortgageService.computeReward`: [5](#0-4) 

For the transaction-fee-reward case, this is not merely "unpaid newly-minted TRX" — it is real value subtracted from `TransactionFeePool`, which is populated by actual user-paid transaction fees: [6](#0-5) 

That amount is deducted from the pool (`saveTransactionFeePool(pool - transactionFeeReward)`) regardless of the witness's vote count, so if the vote count is zero that cycle, real collected fee value is destroyed/stranded rather than distributed or preserved for a future cycle.

### Impact Explanation
This causes silent, permanent loss of witness/voter reward funds (transaction fee pool value and block-reward allowance) whenever an SR's vote count is `0` for a reward cycle. Because `TransactionFeePool` is decremented unconditionally, this is a genuine accounting corruption: real, previously-collected fee value disappears from the system with no account ever being credited it and no mechanism to recover it. This matches the "asset or accounting corruption" acceptance criterion.

### Likelihood Explanation
The trigger condition (an active/standby witness having `0` recorded votes for a given cycle) is a normal, reachable state — it can occur when all voters withdraw/unfreeze their votes for an SR, when a new SR becomes part of the reward-eligible set before votes are recorded for that cycle, or during vote-count transition boundaries handled by `MaintenanceManager.doMaintenance()`. No privileged actor or malicious peer is required; it can occur through ordinary chain operation and normal user actions (unvoting), triggered automatically by block production and cycle maintenance.

### Recommendation
When `voteCount == 0` (or `REMARK`) for a cycle, do not silently drop the credited `reward`/`transactionFeeReward`. Either:
1. Roll the un-distributable reward forward into the next cycle's reward pool for that witness (so it eventually gets included once votes exist) instead of forwarding only the `Vi` value, or
2. Return the corresponding amount back to the `TransactionFeePool` / block-reward budget rather than debiting it unconditionally in `Manager.payReward()`, or
3. Skip crediting `delegationStore.addReward()` (and the corresponding pool deduction) entirely when the witness has zero total votes for the cycle, deferring the payment decision until the reward is guaranteed to be distributable.

### Proof of Concept
1. Configure a witness/SR to be part of the active or standby witness set.
2. Have all voters unfreeze/withdraw votes from that SR such that `WitnessCapsule.getVoteCount()` becomes `0`, and this value is persisted for the current cycle via `MaintenanceManager.doMaintenance()` → `delegationStore.setWitnessVote(nextCycle, witness, 0)`.
3. Let a block be produced by (or fees be attributed to) this witness: `Manager.payReward()` → `mortgageService.payTransactionFeeReward()` / `payBlockReward()` → `payReward()` adds to `delegationStore.addReward(cycle, witness, value)` and, for the fee case, deducts `transactionFeeReward` from `TransactionFeePool` unconditionally.
4. On the next `doMaintenance()` call, `accumulateWitnessVi(cycle, witness, 0)` is invoked with `voteCount == 0`; per lines 136-139 of `DelegationStore.java`, the `Vi` is only forwarded, never incremented by the stored `reward`.
5. Any voter (present or future) querying/withdrawing reward via `MortgageService.computeReward()`/`queryReward()`/`withdrawReward()` will never receive this cycle's reward for that witness, because `getWitnessVi(cycle-1)` and `getWitnessVi(cycle)` are equal (`deltaVi.signum() <= 0` → `continue`), while the value has already been irreversibly deducted from `TransactionFeePool`.

### Citations

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1946-1965)
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
