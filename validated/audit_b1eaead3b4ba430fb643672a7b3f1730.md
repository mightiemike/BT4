<br>

Based on the investigation, I found a concrete analog to this bug class in java-tron's on-chain vote-reward accrual mechanism.

### Title
Voters Can Time Vote/Unvote Transactions around Maintenance-Cycle Boundary to Capture Full-Cycle Witness Rewards for Momentary Stake - (File: `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java`, `chainbase/src/main/java/org/tron/core/service/MortgageService.java`, `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java`)

### Summary
java-tron accrues witness voting rewards using a discrete, once-per-maintenance-cycle "snapshot" of a voter's vote tally, exactly analogous to the reported `Pledge.sol` issue where a reward snapshot is taken at a recurring timestamp and users can time stake/unstake calls around it. In java-tron, `MaintenanceManager.doMaintenance()` runs once per maintenance interval (default 6 hours) and captures each witness's current `voteCount` to accumulate the reward-per-vote index (`Vi`) for that cycle via `DelegationStore.accumulateWitnessVi()` [1](#0-0) . Reward computation later multiplies a voter's vote count by the delta of this Vi index between the cycle in which the vote was recorded and the cycle it is withdrawn, without any accounting for how long within the cycle the vote was actually held [2](#0-1) .

### Finding Description
The maintenance loop, driven by `MaintenanceManager.applyBlock()`, triggers `doMaintenance()` whenever a block's timestamp crosses `nextMaintenanceTime` [3](#0-2) . Inside `doMaintenance()`, witness vote tallies are updated from the `VotesStore` (which reflects votes cast via transactions since the previous maintenance) and then `accumulateWitnessVi` uses the reward paid to that witness plus its current `voteCount` to compute a new cumulative Vi value for the cycle [4](#0-3)  and [5](#0-4) .

Reward for a given account is computed strictly from `beginCycle`/`endCycle` boundaries — the delta between `Vi` at `endCycle-1` and `beginCycle-1`, multiplied by the account's vote count — in `MortgageService.computeReward()` and its TVM-vote counterpart `VoteRewardUtil.computeReward()` [6](#0-5) [2](#0-1) . This is entirely time-blind within a cycle: whether a voter's votes were cast for the whole ~6 hour maintenance interval or added moments before `doMaintenance()` runs (and then cleared/withdrawn moments after the next cycle begins), the account's `votesList` snapshot taken by `doMaintenance()` is what determines the reward for that cycle — there is no intra-cycle time weighting.

A voter can therefore: (1) freeze balance and cast a `VoteWitnessContract` vote just before the block that triggers `doMaintenance()` for cycle N, (2) let that snapshot be captured, (3) immediately after cycle N's maintenance runs, clear the vote or unfreeze (via `UnfreezeBalanceActuator`/`UnfreezeBalanceV2Processor`, which clears votes on insufficient TRON Power) [7](#0-6) , and (4) still receive the full cycle N reward once `withdrawReward`/`queryReward` is called, because reward accrual is keyed to `beginCycle`→`endCycle` deltas rather than actual holding duration.

### Impact Explanation
This allows a voter to capture an entire maintenance cycle's worth of witness voting reward (`Vi`-based allowance credited to `AccountCapsule.allowance`) while only bearing the resource cost (frozen TRX opportunity cost and gas/bandwidth) for a fraction of a block interval around the snapshot, rather than the full cycle. This is a reward/resource accounting integrity issue: honest long-term voters are diluted relative to voters who "snipe" cycle boundaries, and if reward yield per cycle exceeds the cost of freezing/unfreezing and voting/unvoting transactions, this is directly profitable — mirroring the reported `Pledge.sol` issue where snapshot timing is exploitable for profit once rewards exceed gas cost.

### Likelihood Explanation
The behavior requires no privileged access — it is achievable by any account through ordinary broadcast transactions (`FreezeBalanceContract`/`FreezeBalanceV2Contract`, `VoteWitnessContract`, `UnfreezeBalanceContract`/`UnfreezeBalanceV2Contract`, `WithdrawBalanceContract`), and the maintenance cycle boundary time (`getNextMaintenanceTime()`) is a deterministic, publicly-known on-chain value [8](#0-7) , so an attacker can trivially schedule transactions around it.

### Recommendation
Introduce time-weighted (or minimum-holding-duration) accounting for votes within a cycle instead of an all-or-nothing per-cycle snapshot — e.g., pro-rate reward accrual by the fraction of the cycle during which a given vote count was actually held, or require a minimum vote-holding period before a vote counts toward that cycle's `Vi` snapshot, similar to the client's acknowledged fix in the referenced report (counting a stake only if held beyond a threshold duration).

### Proof of Concept
1. Attacker freezes balance and casts `VoteWitnessContract` for witness W in the block immediately preceding the block whose timestamp `>= nextMaintenanceTime` (triggers `MaintenanceManager.doMaintenance()` for cycle N) — this vote is picked up by `countVote(votesStore)` and included in `witness.getVoteCount()` used by `accumulateWitnessVi(curCycle, ...)` [1](#0-0) .
2. Immediately after this maintenance block, attacker submits `UnfreezeBalanceContract`/`UnfreezeBalanceV2Contract` (or clears the vote), removing TRON Power/votes for cycle N+1 onward.
3. After the following maintenance boundary (cycle N+1), attacker submits `WithdrawBalanceContract`, invoking `MortgageService.withdrawReward()` → `computeReward(beginCycle, endCycle, accountCapsule)`, which uses the `Vi` delta accrued for cycle N based on the vote snapshot from step 1 [9](#0-8) .
4. Attacker receives the full cycle N reward despite holding the vote for only a fraction of the ~6-hour interval, having paid only the freeze/vote/unfreeze/withdraw transaction costs.

### Citations

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L57-82)
```java
  public void applyBlock(BlockCapsule blockCapsule) {
    long blockNum = blockCapsule.getNum();
    long blockTime = blockCapsule.getTimeStamp();
    long nextMaintenanceTime = consensusDelegate.getNextMaintenanceTime();
    boolean flag = consensusDelegate.getNextMaintenanceTime() <= blockTime;
    if (flag) {
      if (blockNum != 1) {
        updateWitnessValue(beforeWitness);
        beforeMaintenanceTime = nextMaintenanceTime;
        doMaintenance();
        updateWitnessValue(currentWitness);
      }
      consensusDelegate.updateNextMaintenanceTime(blockTime);
      if (blockNum != 1) {
        //pbft sr msg
        pbftManager.srPrePrepare(blockCapsule, currentWitness,
            consensusDelegate.getNextMaintenanceTime());
      }
    }
    consensusDelegate.saveStateFlag(flag ? 1 : 0);
    //pbft block msg
    if (blockNum == 1) {
      nextMaintenanceTime = consensusDelegate.getNextMaintenanceTime();
    }
    pbftManager.blockPrePrepare(blockCapsule, nextMaintenanceTime);
  }
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L89-127)
```java
  public void doMaintenance() {
    VotesStore votesStore = consensusDelegate.getVotesStore();

    tryRemoveThePowerOfTheGr();

    DynamicPropertiesStore dynamicPropertiesStore = consensusDelegate.getDynamicPropertiesStore();
    DelegationStore delegationStore = consensusDelegate.getDelegationStore();
    if (dynamicPropertiesStore.useNewRewardAlgorithm()) {
      long curCycle = dynamicPropertiesStore.getCurrentCycleNumber();
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }

    Map<ByteString, Long> countWitness = countVote(votesStore);
    if (!countWitness.isEmpty()) {
      List<ByteString> currentWits = consensusDelegate.getActiveWitnesses();

      List<ByteString> newWitnessAddressList = new ArrayList<>();
      consensusDelegate.getAllWitnesses()
          .forEach(witnessCapsule -> newWitnessAddressList.add(witnessCapsule.getAddress()));

      countWitness.forEach((address, voteCount) -> {
        byte[] witnessAddress = address.toByteArray();
        WitnessCapsule witnessCapsule = consensusDelegate.getWitness(witnessAddress);
        if (witnessCapsule == null) {
          logger.warn("Witness capsule is null. address is {}", Hex.toHexString(witnessAddress));
          return;
        }
        AccountCapsule account = consensusDelegate.getAccount(witnessAddress);
        if (account == null) {
          logger.warn("Witness account is null. address is {}", Hex.toHexString(witnessAddress));
          return;
        }
        witnessCapsule.setVoteCount(witnessCapsule.getVoteCount() + voteCount);
        consensusDelegate.saveWitness(witnessCapsule);
        logger.info("address is {} , countVote is {}", witnessCapsule.createReadableString(),
            witnessCapsule.getVoteCount());
      });
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java (L90-110)
```java
  private static long computeReward(long beginCycle, long endCycle,
                                    AccountCapsule accountCapsule, Repository repository) {
    if (beginCycle >= endCycle) {
      return 0;
    }

    long reward = 0;
    for (Protocol.Vote vote : accountCapsule.getVotesList()) {
      byte[] srAddress = vote.getVoteAddress().toByteArray();
      BigInteger beginVi = repository.getDelegationStore().getWitnessVi(beginCycle - 1, srAddress);
      BigInteger endVi = repository.getDelegationStore().getWitnessVi(endCycle - 1, srAddress);
      BigInteger deltaVi = endVi.subtract(beginVi);
      if (deltaVi.signum() <= 0) {
        continue;
      }
      long userVote = vote.getVoteCount();
      reward += deltaVi.multiply(BigInteger.valueOf(userVote))
          .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L89-134)
```java
  public void withdrawReward(byte[] address) {
    if (!dynamicPropertiesStore.allowChangeDelegation()) {
      return;
    }
    AccountCapsule accountCapsule = accountStore.get(address);
    long beginCycle = delegationStore.getBeginCycle(address);
    long endCycle = delegationStore.getEndCycle(address);
    long currentCycle = dynamicPropertiesStore.getCurrentCycleNumber();
    long reward = 0;
    if (beginCycle > currentCycle || accountCapsule == null) {
      return;
    }
    if (beginCycle == currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        return;
      }
    }
    //withdraw the latest cycle reward
    if (beginCycle + 1 == endCycle && beginCycle < currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        reward = computeReward(beginCycle, endCycle, account);
        adjustAllowance(address, reward);
        reward = 0;
        logger.info("Latest cycle reward {}, {}.", beginCycle, account.getVotesList());
      }
      beginCycle += 1;
    }
    //
    endCycle = currentCycle;
    if (CollectionUtils.isEmpty(accountCapsule.getVotesList())) {
      delegationStore.setBeginCycle(address, endCycle + 1);
      return;
    }
    if (beginCycle < endCycle) {
      reward += computeReward(beginCycle, endCycle, accountCapsule);
      adjustAllowance(address, reward);
    }
    delegationStore.setBeginCycle(address, endCycle);
    delegationStore.setEndCycle(address, endCycle + 1);
    delegationStore.setAccountVote(endCycle, address, accountCapsule);
    logger.info("Adjust {} allowance {}, now currentCycle {}, beginCycle {}, endCycle {}, "
            + "account vote {}.", Hex.toHexString(address), reward, currentCycle,
        beginCycle, endCycle, accountCapsule.getVotesList());
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java (L205-223)
```java
    if (VMConfig.allowTvmVote() && !accountCapsule.getVotesList().isEmpty()) {
      long usedTronPower = 0;
      for (Protocol.Vote vote : accountCapsule.getVotesList()) {
        usedTronPower += vote.getVoteCount();
      }
      if (accountCapsule.getTronPower() < usedTronPower * TRX_PRECISION) {
        VoteRewardUtil.withdrawReward(ownerAddress, repo);
        VotesCapsule votesCapsule = repo.getVotes(ownerAddress);
        accountCapsule = repo.getAccount(ownerAddress);
        if (votesCapsule == null) {
          votesCapsule = new VotesCapsule(ByteString.copyFrom(ownerAddress),
              accountCapsule.getVotesList());
        } else {
          votesCapsule.clearNewVotes();
        }
        accountCapsule.clearVotes();
        repo.updateVotes(ownerAddress, votesCapsule);
        repo.updateAccount(ownerAddress, accountCapsule);
      }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L2236-2242)
```java
  public long getNextMaintenanceTime() {
    return Optional.ofNullable(getUnchecked(NEXT_MAINTENANCE_TIME))
        .map(BytesCapsule::getData)
        .map(ByteArray::toLong)
        .orElseThrow(
            () -> new IllegalArgumentException("not found NEXT_MAINTENANCE_TIME"));
  }
```
