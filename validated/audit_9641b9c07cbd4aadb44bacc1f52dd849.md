[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Witness block/transaction-fee rewards accrued while a witness has zero recorded votes are permanently orphaned (unclaimable) - ([File: chainbase/src/main/java/org/tron/core/store/DelegationStore.java])

### Summary
When a super representative (witness) produces blocks and earns block/transaction-fee rewards while `delegationStore`'s recorded vote count for that witness in the current cycle is `0`, the reward amount is credited into the cycle's reward pool via `delegationStore.addReward(...)` but is never reflected into the witness's reward index (`Vi`). Because voter reward computation is entirely driven by delta-`Vi`, that credited reward becomes permanently unclaimable by any voter — functionally identical to the Velocimeter "rewards deposited before first depositor are lost" bug class, except here the trigger is "reward accrued while vote count is 0" rather than "before first depositor".

### Finding Description
Every block, `Manager.payReward()` unconditionally credits the block producer with a block reward (and, if enabled, a transaction fee pool reward) through `MortgageService.payBlockReward()` / `payTransactionFeeReward()`, both of which call the common `payReward()` helper: [5](#0-4) 

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

This unconditionally deposits `value` (minus brokerage) into the current cycle's reward bucket for the witness, with **no check on whether the witness currently has any recorded voter stake** (`delegationStore.getWitnessVote(cycle, witnessAddress)`).

At each maintenance cycle boundary, `MaintenanceManager.doMaintenance()` converts the accumulated per-cycle reward into the voter-facing accounting index `Vi` (a cumulative "reward per vote" accumulator), via: [3](#0-2) 

```java
if (dynamicPropertiesStore.useNewRewardAlgorithm()) {
  long curCycle = dynamicPropertiesStore.getCurrentCycleNumber();
  consensusDelegate.getAllWitnesses().forEach(witness -> {
    delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount());
  });
}
```

`DelegationStore.accumulateWitnessVi` is where the loss occurs — if the vote count for that cycle is `0` (even though `reward > 0`), the delta is simply dropped and `Vi` is not advanced: [2](#0-1) 

```java
public void accumulateWitnessVi(long cycle, byte[] address, long voteCount) {
  BigInteger preVi = getWitnessVi(cycle - 1, address);
  long reward = getReward(cycle, address);
  if (reward == 0 || voteCount == 0) { // Just forward pre vi
    if (!BigInteger.ZERO.equals(preVi)) { // Zero vi will not be record
      setWitnessVi(cycle, address, preVi);
    }
  } else { // Accumulate delta vi
    ...
  }
}
```

The same skip-on-zero-vote pattern exists in the legacy reward algorithm in `MortgageService.computeReward(cycle, votes)`: [6](#0-5) 

```java
long totalVote = delegationStore.getWitnessVote(cycle, srAddress);
if (totalVote == DelegationStore.REMARK || totalVote == 0) {
  continue;
}
```

Because the per-cycle witness vote snapshot (`delegationStore.getWitnessVote(cycle, ...)`) is fixed at the *start* of the cycle (set during the previous `doMaintenance()` from `witness.getVoteCount()`), any witness that is actively producing blocks (and thus earning rewards through `payReward`) while its snapshot vote count for that cycle is `0` — for example, a newly registered witness that becomes part of the active/standby set before receiving its first vote, or an existing witness whose voters fully withdrew right at the cycle boundary — will have that cycle's entire reward silently discarded: the TRX is added to `delegationStore`'s reward bucket (so it is not literally burned from total token accounting bookkeeping), but no voter can ever claim it since the reward-per-vote index (`Vi`) never reflects it and no future voter's `computeReward` walk can retroactively recover the skipped cycle.

This mirrors the reported bug class 1:1: rewards deposited into a reward-accounting structure "before" (or during a period without) any stake-holder capable of claiming a pro-rata share are permanently lost, rather than being rolled forward or reverted.

### Impact Explanation
This results in permanent, irrecoverable loss of witness voter rewards for the affected cycle(s). Reward computation is fully automatic and permissionless — it runs on every block via `Manager.payReward()` and every maintenance cycle via `MaintenanceManager.doMaintenance()`, with no admin action required, so the condition (an active/producing witness with zero recorded votes for the cycle) is reachable without any privileged actor. This is loss-of-funds impact for the protocol's voter reward pool (funds effectively become unclaimable forever), matching the low/informational-to-medium classification given to the analogous Sherlock report, since the funds are not stolen but simply orphaned.

### Likelihood Explanation
The precondition — a witness being part of the active block-producing/standby set while its snapshot vote count for the current cycle is `0` — is plausible in real operational scenarios: newly created witnesses (`WitnessCapsule` starts at `voteCount = 0`) that become active before receiving votes in networks with fewer registered witnesses than the standby/active slot count, or witnesses whose entire voter base unvotes at a cycle boundary. Because block/transaction-fee reward crediting is unconditional and happens every block regardless of vote state, the likelihood scales with block production frequency, similar to the "automatic and permissionless" argument accepted in the original report.

### Recommendation
In `DelegationStore.accumulateWitnessVi` (and the legacy `MortgageService.computeReward(cycle, votes)` path), when `reward > 0` but `voteCount == 0`, do not silently drop the reward. Instead, either (a) roll the un-attributable reward forward to the next cycle's reward bucket for that witness so it can be distributed once votes exist, or (b) redirect it to a designated fallback (e.g., the transaction fee pool or block reward pool) rather than leaving it permanently unclaimable.

### Proof of Concept
1. Register a new witness `W` (`WitnessCapsule` created with `voteCount = 0`) that, due to network configuration (e.g., fewer witnesses than `WITNESS_STANDBY_LENGTH`/active slots), is immediately part of the active/standby witness set and begins producing blocks before any account votes for it.
2. For each block `W` produces in cycle `N`, `Manager.payReward()` → `MortgageService.payBlockReward()`/`payTransactionFeeReward()` → `payReward()` calls `delegationStore.addReward(N, W, value)`, accumulating a nonzero reward for cycle `N` while `delegationStore.getWitnessVote(N, W) == 0` (snapshot taken at the previous maintenance).
3. At the next maintenance boundary, `MaintenanceManager.doMaintenance()` calls `delegationStore.accumulateWitnessVi(N, W, 0)` (vote count still 0 for that snapshot), which takes the `voteCount == 0` branch and does not advance `Vi` — the reward recorded in step 2 is now unreachable by any future `computeReward`/`VoteRewardUtil.computeReward` walk over `Vi` deltas, because those functions only look at `deltaVi = endVi - beginVi`, which will not include the dropped cycle's reward.
4. Even if accounts later vote for `W` and call `withdrawReward`, `MortgageService.computeReward(beginCycle, endCycle, accountCapsule)` / `VoteRewardUtil.computeReward` sums only `deltaVi` between cycles, permanently excluding the reward accrued in cycle `N`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L69-87)
```java
  public void payBlockReward(byte[] witnessAddress, long value) {
    logger.debug("Pay {} block reward {}.", Hex.toHexString(witnessAddress), value);
    payReward(witnessAddress, value);
  }

  public void payTransactionFeeReward(byte[] witnessAddress, long value) {
    logger.debug("Pay {} transaction fee reward {}.", Hex.toHexString(witnessAddress), value);
    payReward(witnessAddress, value);
  }

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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1946-1953)
```java
  private void payReward(BlockCapsule block) {
    WitnessCapsule witnessCapsule =
        chainBaseManager.getWitnessStore().getUnchecked(block.getInstance().getBlockHeader()
            .getRawData().getWitnessAddress().toByteArray());
    if (getDynamicPropertiesStore().allowChangeDelegation()) {
      mortgageService.payBlockReward(witnessCapsule.getAddress().toByteArray(),
          getDynamicPropertiesStore().getWitnessPayPerBlock());
      mortgageService.payStandbyWitness();
```
