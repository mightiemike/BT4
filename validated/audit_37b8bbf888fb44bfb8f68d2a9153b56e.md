### Title
Witness rewards accrued while a witness has zero total votes are permanently lost in the reward-per-share (Vi) accounting - (File: chainbase/src/main/java/org/tron/core/store/DelegationStore.java)

### Summary
The reported bug class is a classic "zero-shares" accounting flaw: rewards are collected into a pool contract, but the code refuses to convert those rewards into claimable units when the "shares" denominator (`totalSupply()`/`totalVote`) is zero, so the funds become permanently stuck/unclaimable instead of being retried or held until shares exist. Java-tron's DPoS reward-per-share ("Vi", value-per-vote) mechanism in `DelegationStore.accumulateWitnessVi` and its equivalent in `RewardViCalService.accumulateWitnessVi` has the same structural flaw: when a witness's vote count for a cycle is `0`, any reward booked for that witness in that cycle is silently discarded (never converted to Vi delta) and can never be claimed by any voter, even retroactively once the witness receives votes later.

### Finding Description
Block and transaction-fee rewards are unconditionally accrued per witness per cycle via `MortgageService.payReward()`, which calls `delegationStore.addReward(cycle, witnessAddress, value)` regardless of whether the witness currently has any votes: [1](#0-0) 

At maintenance time, these accrued rewards are supposed to be converted into a per-vote index ("Vi") that voters later use to compute their share of the reward: [2](#0-1) 

The guard `if (reward == 0 || voteCount == 0) { // Just forward pre vi }` mirrors the reported `if (StakingVault(vault).totalSupply() > 0)` pattern: when `voteCount` (the analog of `totalSupply()`/shares) is `0` for that cycle, the previously-booked `reward` for that cycle is dropped — the Vi is simply forwarded unchanged instead of being deferred or redistributed. This is invoked every maintenance cycle for every witness in `MaintenanceManager.doMaintenance()`: [3](#0-2) 

The identical logic (duplicated) exists in the batch-computation service used to backfill Vi values: [4](#0-3) 

The legacy (pre-Vi) reward path has the same effect, just phrased differently: `MortgageService.computeReward(cycle, votes)` skips any cycle where `totalVote == 0`, so a voter's share of a reward booked while total votes were `0` can never be computed for anyone: [5](#0-4) 

In all three code paths, `delegationStore.addReward` has already stored the value for that `(cycle, witnessAddress)` key, but there is no mechanism that later re-attributes it once votes appear — the value is orphaned in `DelegationStore`'s reward key-space forever, exactly analogous to the WETH remaining stuck in `SubscribeRegistry` when `StakingVault(vault).totalSupply() == 0`.

### Impact Explanation
Any witness cycle in which a witness earns block/standby/transaction-fee reward while carrying zero votes (e.g., a newly created witness before its first vote is counted, or an existing witness that temporarily loses all votes for a cycle) results in that cycle's reward being permanently unclaimable by any account — not merely delayed. Because rewards are paid on essentially every produced block (`Manager.payReward` → `mortgageService.payBlockReward`/`payTransactionFeeReward`), and vote counts are only updated once per maintenance cycle in `MaintenanceManager.doMaintenance`, this is a structural, not merely edge-case, occurrence for freshly registered witnesses. This causes an accounting/settlement divergence: TRX (in the form of allowance credits) that should eventually be distributable to voters is effectively burned/lost from the reward pool's perspective, an underpriced/lost-value condition analogous to the original finding.

### Likelihood Explanation
This is reachable by any unprivileged user who calls `WitnessCreateActuator` to register as a new witness — no special privilege is required beyond paying the witness creation fee: [6](#0-5) 

A newly created witness naturally starts with `voteCount == 0` and can still be selected/paid via `IncentiveManager.reward()` for the standby list, or simply accrue transaction-fee/block reward if it becomes an active SR before receiving its first counted vote. This is a normal operational sequence (not a contrived or purely theoretical scenario), so the likelihood of at least one cycle's reward being lost per newly bootstrapped witness is high.

### Recommendation
Do not drop the reward when `voteCount == 0`. Instead, either (a) carry the un-distributed reward amount forward and add it into the next cycle in which `voteCount > 0` (so it eventually gets folded into the Vi delta), or (b) route it to a global/witness treasury/black-hole account so it is not silently lost, mirroring how the original report's fix removed the `totalSupply() > 0` gating and let the first depositor's reward flow through. Concretely, in `DelegationStore.accumulateWitnessVi` and `RewardViCalService.accumulateWitnessVi`, when `voteCount == 0` but `reward > 0`, accumulate the un-attributed reward into a carry-over bucket keyed by witness and apply it against the next cycle's `deltaVi` computation instead of discarding it.

### Proof of Concept
1. Register a new witness `W` via `WitnessCreateActuator` in cycle `N` (its `voteCount` is `0` since no votes have been cast/counted yet for it in this cycle).
2. Let `W` produce a block (or receive standby reward) in cycle `N`; `MortgageService.payReward()` calls `delegationStore.addReward(N, W, value)`, storing `value` for `(N, W)`.
3. At maintenance for cycle `N`, `MaintenanceManager.doMaintenance()` calls `delegationStore.accumulateWitnessVi(N, W, witness.getVoteCount())` with `voteCount == 0`; per the guard, the Vi is only forwarded, not incremented — `value` is never reflected in any Vi delta.
4. In cycle `N+1`, a voter casts votes for `W`, and `witness.getVoteCount()` becomes non-zero going forward.
5. That voter calls `withdrawReward`/`queryReward`; `computeReward` in `MortgageService`/`VoteRewardUtil` only ever reads `deltaVi` differences between cycles they voted in — the cycle-`N` reward is never included in any subsequent `deltaVi`, so it is permanently unclaimable by anyone, matching the reported "stuck reward" pattern.

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

**File:** actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java (L121-141)
```java
  private void createWitness(final WitnessCreateContract witnessCreateContract)
      throws BalanceInsufficientException {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    WitnessStore witnessStore = chainBaseManager.getWitnessStore();
    //Create Witness by witnessCreateContract
    final WitnessCapsule witnessCapsule = new WitnessCapsule(
        witnessCreateContract.getOwnerAddress(),
        0,
        witnessCreateContract.getUrl().toStringUtf8());

    logger.debug("createWitness,address[{}]", witnessCapsule.createReadableString());
    witnessStore.put(witnessCapsule.createDbKey(), witnessCapsule);
    AccountCapsule accountCapsule = accountStore
        .get(witnessCapsule.createDbKey());
    accountCapsule.setIsWitness(true);
    if (dynamicStore.getAllowMultiSign() == 1) {
      accountCapsule.setDefaultWitnessPermission(dynamicStore);
    }
    accountStore.put(accountCapsule.createDbKey(), accountCapsule);
    long cost = dynamicStore.getAccountUpgradeCost();
```
