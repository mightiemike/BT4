This confirms the analog. The block-producing witness earns rewards via `Manager.payReward` (`payBlockReward`), which calls `MortgageService.payReward` → `delegationStore.addReward(cycle, witnessAddress, value)`, independent of that witness's current vote count. [1](#0-0) [2](#0-1) 

Separately, per-cycle vote counts ("liquidity") used to allocate that reward to voters are stored via `DelegationStore.setWitnessVote`, snapshotted once per maintenance cycle from `witness.getVoteCount()` — this can be `0` for a witness whose voters have fully unvoted, or for a witness present in the active set with no votes yet (e.g. genesis witnesses defined with `voteCount = 0` in `GenesisConfig.WitnessConfig`). [3](#0-2) [4](#0-3) 

When the new (Vi-based) reward algorithm accumulates this cycle's witness reward into the per-cycle "value-per-vote" accumulator (`Vi`), it explicitly skips accumulation whenever `voteCount == 0`, silently dropping the deposited reward rather than crediting it to anyone (owner/treasury included): [5](#0-4) [6](#0-5) 

This is called every maintenance cycle from `MaintenanceManager.doMaintenance()`: [7](#0-6) 

The old (pre-Vi) reward algorithm has the identical pattern in `MortgageService.computeReward(cycle, votes)`, which `continue`s (skips) whenever `totalVote == 0`, meaning the reward stored under that cycle/witness key is never referenced by any voter's claim path: [8](#0-7) 

### Title
Block/transaction-fee rewards deposited during zero-vote ("zero liquidity") cycles are permanently lost instead of credited to the owner - (File: chainbase/src/main/java/org/tron/core/store/DelegationStore.java, chainbase/src/main/java/org/tron/core/service/RewardViCalService.java, chainbase/src/main/java/org/tron/core/service/MortgageService.java)

### Summary
The DPoS voting-reward accounting model in java-tron mirrors the Paladin Valkyrie `_updateRewardState` reward-per-share pattern: rewards accrue to witnesses independent of the number of active voters ("liquidity"), while the payout share to voters is computed as `reward / totalVote` (old algorithm) or accumulated into a running value-per-vote index `Vi` (new algorithm). When `totalVote`/`voteCount` for a witness is `0` for a given cycle, both algorithms silently discard the reward that was deposited for that cycle instead of crediting it to the owner/treasury, exactly matching the reported bug class ("deposited rewards get stuck ... when there is no pool liquidity").

### Finding Description
Rewards for block production and transaction fees are deposited unconditionally per-witness-per-cycle via `MortgageService.payReward()`, which calls `delegationStore.addReward(cycle, witnessAddress, value)` regardless of the witness's current vote total. [2](#0-1) 

Later, this deposited reward is supposed to be distributed proportionally to voters based on their share of the witness's total votes in that cycle. In the legacy algorithm, `MortgageService.computeReward(cycle, votes)` looks up `totalVote = delegationStore.getWitnessVote(cycle, srAddress)` and simply `continue`s (drops the reward for that vote/cycle pair) if `totalVote == 0`. [8](#0-7) 

In the newer Vi-accumulator algorithm (run every maintenance cycle by `MaintenanceManager.doMaintenance()`), `DelegationStore.accumulateWitnessVi()` and its duplicate `RewardViCalService.accumulateWitnessVi()` compute the delta to add to the running per-vote index as `reward * DECIMAL / voteCount`. When `voteCount == 0`, the code takes the `if (reward == 0 || voteCount == 0)` branch and only "forwards" the previous `Vi` value unchanged — the `reward` value for that cycle is never folded into `Vi` and is not routed anywhere else (no owner/treasury credit exists in this code path). [5](#0-4) [6](#0-5) 

The vote-count snapshot used for this check (`witness.getVoteCount()`) is taken once per maintenance cycle and stored for the *next* cycle via `delegationStore.setWitnessVote(nextCycle, ...)`. [3](#0-2)  A witness can have `voteCount == 0` for an entire cycle, e.g., immediately after genesis (witnesses can be configured in `GenesisConfig.WitnessConfig` with `voteCount = 0`) or after all voters fully unvote via `UnfreezeBalanceV2Actuator`/withdraw-vote flows while the witness remains in the active set until the next maintenance boundary. [4](#0-3)  During such a cycle, `payReward` is still invoked whenever this witness produces a block (`Manager.payReward` → `mortgageService.payBlockReward`/`payTransactionFeeReward`), depositing TRX that later cannot be attributed to any voter and is not attributed to the owner/treasury either. [9](#0-8) 

### Impact Explanation
TRX deposited into `delegationStore.addReward(cycle, witnessAddress, value)` during a zero-vote cycle becomes permanently unclaimable: no voter has a vote entry for that witness/cycle to claim against (old algorithm skip), and the Vi accumulator never incorporates it (new algorithm skip). Unlike the Paladin fix which recommends crediting an `accumulatedFees`-style owner/treasury balance, java-tron's implementation has no equivalent fallback, so the value is simply lost from the accounting system — a genuine, if narrow, loss-of-funds bug in the network's own incentive accounting.

### Likelihood Explanation
This requires a witness to be actively producing blocks/receiving standby pay while having zero recorded votes for the relevant cycle — an edge case (e.g., genesis witnesses before any votes are cast, or a witness whose voters fully unvote mid-cycle while it remains active until the next maintenance switch). It is uncommon on a live network with an established witness set but is a real, reachable state given the vote-count snapshot timing described above, matching the "highly improbable but real" characterization in the original report.

### Recommendation
Mirror the referenced fix: when `totalVote`/`voteCount` is `0` for a cycle in which a reward was deposited for a witness, route that reward to an explicit fallback (e.g., accumulate it into a chain-owned/treasury balance such as the burn pool or `TransactionFeePool`) instead of silently discarding it in `MortgageService.computeReward()`, `DelegationStore.accumulateWitnessVi()`, and `RewardViCalService.accumulateWitnessVi()`.

### Proof of Concept
1. A witness is registered with `voteCount = 0` (e.g., a fresh witness right after `WitnessCreateContract` execution, or one whose voters fully withdraw via unvote/unfreeze before the next maintenance cycle) and remains in the active witness list until the next `MaintenanceManager.doMaintenance()` boundary.
2. The witness produces a block; `Manager.payReward()` calls `mortgageService.payBlockReward(...)`/`payTransactionFeeReward(...)`, which deposits value via `delegationStore.addReward(cycle, witnessAddress, value)`.
3. At the next maintenance cycle, `MaintenanceManager.doMaintenance()` calls `delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount())` with `voteCount == 0`, which forwards the previous `Vi` unchanged and discards the deposited reward.
4. No voter can subsequently claim this reward via `MortgageService.withdrawReward()`/`queryReward()` because it was never incorporated into `Vi`, and no owner/treasury credit was made — the funds are permanently stranded in the ledger.

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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L154-162)
```java
    if (dynamicPropertiesStore.allowChangeDelegation()) {
      long nextCycle = dynamicPropertiesStore.getCurrentCycleNumber() + 1;
      dynamicPropertiesStore.saveCurrentCycleNumber(nextCycle);
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.setBrokerage(nextCycle, witness.createDbKey(),
            delegationStore.getBrokerage(witness.createDbKey()));
        delegationStore.setWitnessVote(nextCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }
```

**File:** common/src/main/java/org/tron/core/config/args/GenesisConfig.java (L36-42)
```java
  @Getter
  @Setter
  public static class WitnessConfig {
    private String address = "";
    private String url = "";
    private long voteCount = 0;
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
