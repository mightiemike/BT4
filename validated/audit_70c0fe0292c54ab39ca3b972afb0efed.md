Confirmed: `updateWitness()` sets active witnesses from **all** registered witnesses (up to `MAX_ACTIVE_WITNESS_NUM`), sorted by vote count, without any minimum-vote filter [1](#0-0) . This means if a network has fewer than `MAX_ACTIVE_WITNESS_NUM` (127) candidates — genesis/bootstrap of any private/test/new chain, or a mature chain where registered witnesses drop below 127 — witnesses with `voteCount == 0` become active, get scheduled to produce blocks, and receive rewards, exactly the precondition needed to trigger this bug.

### Title
Lost/Stuck Delegator Rewards When Witness Vote Count is 0 in `DelegationStore.accumulateWitnessVi` - (File: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java`)

### Summary
`java-tron`'s new reward algorithm distributes voter rewards MasterChef-style via a per-witness reward-per-vote accumulator (`Vi`), analogous to Tokemak's `rewardPerTokenStored`. When a witness's vote count (the "total supply" in this analogy) is `0` for a cycle, the accumulator refuses to fold in that cycle's already-queued reward, permanently losing it, exactly as described in the M-2 report.

### Finding Description
Every maintenance cycle, `MaintenanceManager.doMaintenance()` calls `delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount())` for every witness when `useNewRewardAlgorithm()` is enabled [2](#0-1) . Rewards are queued into `DelegationStore` independently, whenever a witness produces a block, via `MortgageService.payReward()` → `delegationStore.addReward(cycle, witnessAddress, value)` [3](#0-2) .

The accumulation logic is:
```java
public void accumulateWitnessVi(long cycle, byte[] address, long voteCount) {
    BigInteger preVi = getWitnessVi(cycle - 1, address);
    long reward = getReward(cycle, address);
    if (reward == 0 || voteCount == 0) { // Just forward pre vi
      if (!BigInteger.ZERO.equals(preVi)) {
        setWitnessVi(cycle, address, preVi);
      }
    } else {
      BigInteger deltaVi = BigInteger.valueOf(reward)
          .multiply(DECIMAL_OF_VI_REWARD)
          .divide(BigInteger.valueOf(voteCount));
      setWitnessVi(cycle, address, preVi.add(deltaVi));
    }
}
``` [4](#0-3) 

When `voteCount == 0` but `reward != 0` (block/tx-fee reward was already queued for the cycle via `addReward`), the `Vi` index is not advanced — it is simply carried forward unchanged, so the reward accrued in that cycle is never reflected in the index used to compute delegator claims. Delegator reward computation (`computeReward` in `MortgageService.java` and `VoteRewardUtil.java`) derives rewards purely from `deltaVi = endVi - beginVi` multiplied by the user's vote count [5](#0-4) ; since `Vi` never grows for that cycle, no delegator can ever claim that reward. The witness itself is only paid its brokerage share instantly via `adjustAllowance` in `payReward` — the remainder (the bulk of the reward, intended for voters) is left in the `DelegationStore` reward key forever unreachable, because it is never picked up in any subsequent `Vi` calculation (only the current cycle's `reward` value is read once).

The precondition (`voteCount == 0` for an active, block-producing witness) is reachable because `DposService.updateWitness()` populates the active witness set from **all** registered witnesses sorted by vote count, capped at `MAX_ACTIVE_WITNESS_NUM`, with no minimum-vote-count filter [1](#0-0) . Any chain with fewer registered witnesses than `MAX_ACTIVE_WITNESS_NUM` (127) — genesis/bootstrap of private or newly launched networks, or any period where registered witness count falls below 127 — will include zero-vote witnesses as active block producers, who will earn block and transaction-fee rewards while their `voteCount` is `0`.

### Impact Explanation
Reward tokens paid to a witness with `0` votes during a cycle are permanently stuck/lost from the perspective of delegators — no voter can ever claim them because the reward-per-vote index (`Vi`) never advances for that cycle. This is a direct accounting/loss-of-funds bug affecting the DPoS reward distribution mechanism, matching the "underpriced/lost distributed rewards" impact class.

### Likelihood Explanation
This is deterministic, not probabilistic: it triggers any time an active, block-producing witness has `voteCount == 0` for a cycle, which is guaranteed at genesis/bootstrap of any network (including private/test networks, and potentially early mainnet-like deployments) where the number of registered witnesses is below `MAX_ACTIVE_WITNESS_NUM`, since `updateWitness()` unconditionally admits zero-vote witnesses into the active set.

### Recommendation
When `voteCount == 0` but `reward != 0` in `accumulateWitnessVi`, do not silently drop the reward — either (a) prevent witnesses with `0` votes from being included in the active/block-producing set, or (b) retain the un-distributed reward (e.g., roll it forward and add it to the next cycle's reward for that witness once `voteCount > 0`, or redirect it, e.g., to the witness's own account or a treasury) instead of discarding it when `voteCount == 0`.

### Proof of Concept
1. Launch a chain (or consider genesis/bootstrap of any network) with fewer registered witnesses than `MAX_ACTIVE_WITNESS_NUM`, so a witness `W` with `voteCount == 0` is included in `activeWitnesses` by `DposService.updateWitness()`.
2. `W` is scheduled and produces blocks; `MortgageService.payBlockReward`/`payTransactionFeeReward` call `payReward()`, which calls `delegationStore.addReward(cycle, W, value)`, queuing a non-zero reward for `W` at `cycle`.
3. At the next maintenance, `MaintenanceManager.doMaintenance()` calls `delegationStore.accumulateWitnessVi(cycle, W, W.getVoteCount())` with `voteCount == 0`.
4. Inside `accumulateWitnessVi`, since `voteCount == 0`, the `else` branch (which computes `deltaVi` and updates `Vi`) is skipped; `Vi` for `cycle` is just `preVi` (unchanged).
5. Any delegator (even one who later votes for `W`) computing their reward via `computeReward(beginCycle, endCycle, ...)` will get `deltaVi = endVi - beginVi = 0` for that cycle, contributing zero reward, even though `getReward(cycle, W)` still holds the non-zero value in the DB — it is now permanently unreachable through the normal reward-claim path.

### Citations

**File:** consensus/src/main/java/org/tron/consensus/dpos/DposService.java (L178-186)
```java
  public void updateWitness(List<ByteString> list) {
    consensusDelegate.sortWitness(list);
    if (list.size() > MAX_ACTIVE_WITNESS_NUM) {
      consensusDelegate
          .saveActiveWitnesses(list.subList(0, MAX_ACTIVE_WITNESS_NUM));
    } else {
      consensusDelegate.saveActiveWitnesses(list);
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
