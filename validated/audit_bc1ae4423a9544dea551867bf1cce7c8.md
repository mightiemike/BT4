### Title
Witness Rewards Permanently Stuck When Vote Count Is Zero In `accumulateWitnessVi` - (File: chainbase/src/main/java/org/tron/core/store/DelegationStore.java)

### Summary
The Bribe bug's root cause is a timing/precondition mismatch: rewards get recorded into a per-epoch bucket before the mechanism that makes them claimable (a non-zero denominator to distribute against) exists, and once that epoch closes there is no path to ever retrieve them. Java-tron's SR (Super Representative) voting-reward accounting has an analogous structural flaw in `DelegationStore.accumulateWitnessVi`: if a witness earns block/transaction-fee reward in a cycle where its vote count used for the Vi (value-per-share) calculation is `0`, the reward is silently dropped from the reward-per-share index forever, with no rescue path.

### Finding Description
Block and transaction-fee rewards for a witness are accumulated per cycle via `MortgageService.payReward()`, which calls `delegationStore.addReward(cycle, witnessAddress, value)` [1](#0-0) .

At the end of each cycle, `MaintenanceManager.doMaintenance()` converts that stored reward into a per-vote index (`Vi`) that voters later use to compute their share:
```
delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount());
``` [2](#0-1) 

The core logic is:
```java
public void accumulateWitnessVi(long cycle, byte[] address, long voteCount) {
    BigInteger preVi = getWitnessVi(cycle - 1, address);
    long reward = getReward(cycle, address);
    if (reward == 0 || voteCount == 0) { // Just forward pre vi
      if (!BigInteger.ZERO.equals(preVi)) {
        setWitnessVi(cycle, address, preVi);
      }
    } else { // Accumulate delta vi
      BigInteger deltaVi = BigInteger.valueOf(reward)
          .multiply(DECIMAL_OF_VI_REWARD)
          .divide(BigInteger.valueOf(voteCount));
      setWitnessVi(cycle, address, preVi.add(deltaVi));
    }
}
``` [3](#0-2) 

If `reward > 0` but `voteCount == 0` at the moment `doMaintenance` runs, the branch simply carries forward the previous `Vi` unchanged (or writes nothing at all if `preVi` is also zero) — the `reward` value already persisted via `addReward` for that cycle is **never folded into `Vi`**. Since all downstream voter payouts (`MortgageService.withdrawReward` / `queryReward`, and the TVM `VoteRewardUtil`) compute rewards purely as the delta of `Vi` between a voter's `beginCycle` and `endCycle` multiplied by the voter's vote weight [4](#0-3) , a reward amount that never entered `Vi` can **never** be paid to any voter. There is no compensating mechanism, no re-accumulation on a later cycle, and no way to "claim" the orphaned `reward` bucket for that cycle — it is permanently orphaned in the `delegation` store, mirroring the Velodrome `Bribe` pattern where `tokenRewardsPerEpoch[token][adjustedTstamp]` becomes unclaimable once its epoch closes.

This is structurally identical to the external bug class: reward tokens are credited to a time-bucketed store keyed by cycle, but the downstream distribution formula (`Vi` delta / vote-weight ratio) has a hard dependency on a nonzero denominator (`voteCount`) that is not guaranteed to be nonzero at the exact moment the reward is recorded, and once the cycle boundary passes, the reward for that specific cycle is unrecoverable.

### Impact Explanation
A witness that is part of the active set (and therefore produces blocks and earns rewards) but has `voteCount == 0` for a given cycle — which is a normal accumulator field read as-of that cycle's maintenance, not something a voter/attacker controls transaction-by-transaction — causes that cycle's TRX reward to be permanently and irrecoverably lost from the reward pool. This is a value-corruption/asset-loss bug in the resource/reward accounting subsystem: TRX that should be distributable to voters (and reflected in total supply accounting via `Vi`) becomes stuck, exactly as in the referenced report where bribe tokens became permanently stuck in the `Bribe` contract with no rescue path.

### Likelihood Explanation
The active witness set is chosen purely by `WitnessCapsule.getVoteCount()` ranking in `DposService.updateWitness` / `consensusDelegate.sortWitness` [5](#0-4) , with no floor requiring a minimum nonzero vote count before a witness can be admitted to the active/standby set. On mainnet, genesis witnesses are seeded with very large (~10^8) vote counts, making `voteCount == 0` unlikely in the default mainnet genesis config [6](#0-5) . However, on any permissionless/low-participation network (private chains, freshly bootstrapped networks with fewer registered witnesses than `MAX_ACTIVE_WITNESS_NUM`, or witnesses whose voters fully unvote/unfreeze between maintenance cycles) a witness can legitimately have `voteCount == 0` while still occupying an active slot and producing blocks that earn rewards, causing per-cycle reward loss. I could not fully verify from static reading alone whether there is a guard elsewhere (e.g., in `WitnessStore`/`countVote`) that excludes zero-vote witnesses from ever being scheduled to produce blocks; this would need runtime/test verification to establish exact preconditions for triggering the bug on a live network.

### Recommendation
When `accumulateWitnessVi` detects `reward > 0 && voteCount == 0`, do not silently drop the reward. Instead, either (a) roll the undistributed reward forward into the next cycle's `reward` bucket for that witness so it eventually gets folded into `Vi` once `voteCount` becomes nonzero, or (b) redirect it to the witness's own allowance (since brokerage already goes to the witness, treating undistributable voter rewards the same way avoids permanent loss), or (c) add a floor requiring `voteCount > 0` for any witness before it can be selected as active/standby, preventing the zero-denominator scenario from ever arising during reward accrual.

### Proof of Concept
1. Configure a private/test network where the number of registered witnesses is below `MAX_ACTIVE_WITNESS_NUM`, or arrange for a witness's voters to fully unfreeze/unvote such that `WitnessCapsule.getVoteCount()` reads `0` at the time `MaintenanceManager.doMaintenance()` runs for cycle `N` (per `MaintenanceManager.doMaintenance`, lines 96-101).
2. Ensure the witness is still in `consensusDelegate.getAllWitnesses()`/active set and produces at least one block in cycle `N`, causing `MortgageService.payReward()` to call `delegationStore.addReward(N, witnessAddress, value)`.
3. At the `doMaintenance()` boundary for cycle `N`, `accumulateWitnessVi(N, witnessAddress, 0)` is invoked; since `voteCount == 0`, the branch at `DelegationStore.java:136-139` executes, and the `reward` recorded in step 2 is never converted into `Vi`.
4. Any later voter querying/withdrawing reward via `MortgageService.queryReward`/`withdrawReward` (or the TVM `VoteRewardUtil`) computes rewards strictly from `Vi` deltas and can never recover the cycle-`N` reward for that witness — it remains permanently orphaned in the `delegation` store with no code path to reclaim it.

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

**File:** common/src/main/resources/reference.conf (L598-609)
```text
  # Initial Super Representatives at block 0.
  # Fields:
  #   address   – Base58Check-encoded SR address (T...)
  #   url       – SR's public URL (informational only, stored on-chain)
  #   voteCount – initial vote count; seeds SR ranking before any user votes are cast
  # The 27 witnesses with the highest voteCount produce the first round of blocks.
  witnesses = [
    {
      address: THKJYuUmMKKARNf7s2VT51g5uPY6KEqnat, # Base58Check-encoded SR address (T...)
      url = "http://GR1.com",                       # SR's public URL (informational only, stored on-chain)
      voteCount = 100000026                         # initial vote count; seeds SR ranking before any user votes are cast
    },
```
