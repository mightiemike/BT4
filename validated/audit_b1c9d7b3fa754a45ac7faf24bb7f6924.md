### Title
Unfair cycle-granular voting-reward distribution allows users to earn full-cycle SR rewards for votes held only seconds - ([File: consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java])

### Summary
java-tron's witness voting reward system (the "new reward algorithm") settles rewards purely by cycle number boundaries rather than by actual duration a vote was active, exactly analogous to the reported bug class where users "take their share of rewards in cycles they stayed for just a couple of seconds."

### Finding Description
Rewards for SR votes are accrued using a per-cycle value-index (`Vi`) mechanism. At every maintenance transition, `MaintenanceManager.doMaintenance()`:

1. Accumulates the just-finished cycle's `Vi` for each witness using the witness's *current* `voteCount` (i.e., the vote weight that has been active for the entire cycle that is ending): [1](#0-0) 
2. Tallies newly cast/removed votes accumulated during that cycle via `countVote(votesStore)` and folds them into `witnessCapsule.voteCount`: [2](#0-1) 
3. Advances `currentCycleNumber` and stores the *updated* vote count as the vote weight for `nextCycle`: [3](#0-2) 

A user's own reward accrual (`MortgageService.withdrawReward`/`computeReward`) is likewise tracked only by `beginCycle`/`endCycle` cycle numbers, not by actual time held: [4](#0-3)  and reward is computed as `deltaVi * userVote` for the whole `[beginCycle, endCycle)` interval regardless of how much of that cycle the vote was actually active: [5](#0-4) 

Because vote activation/removal is batched at maintenance boundaries (`VotesCapsule` `oldVotes`/`newVotes`, `VoteWitnessActuator.countVoteAccount`), a vote cast in the very last block before a maintenance is treated identically — for the purposes of subsequent-cycle reward eligibility — to a vote cast at the very start of the previous cycle: [6](#0-5) [7](#0-6) 

This is the same root cause as the reported issue: cycle start/end are recorded as "the current pool cycle, regardless of the cycle's progress," so a participant present for mere seconds around a cycle boundary can claim a full cycle's worth of rewards, and conversely a vote/stake can be pulled out with only a couple of seconds of exposure to a cycle it is credited for.

### Impact Explanation
A voter can freeze balance and cast a vote in the final block before a `doMaintenance()` call, then immediately unfreeze/unvote in the first block of the following cycle (`UnfreezeBalanceV2Processor.execute` / `VoteWitnessActuator`), yet because vote-count changes only take effect at the *next* maintenance boundary, the account remains counted as an active voter for the entire just-started cycle. When `withdrawReward`/`queryReward` is later called, `computeReward` pays out the full `deltaVi` for that cycle as if the vote had been held the whole time. This dilutes rewards owed to genuinely long-term voters and lets short-term "flash voters" arbitrage reward cycles — an accounting/economic fairness defect in the resource/reward accounting subsystem, reachable by any account via ordinary `VoteWitnessContract` / `UnfreezeBalanceV2Contract` broadcast transactions (or the equivalent TVM-vote precompile path in `VoteRewardUtil`).

### Likelihood Explanation
High. No privileged role is required — any account holding enough TRX to freeze/vote can time an ordinary vote/unvote transaction relative to known epoch/maintenance boundaries (maintenance interval is a public, predictable chain parameter), and the actuators involved (`VoteWitnessActuator`, `UnfreezeBalanceV2Actuator`/`UnfreezeBalanceV2Processor`) are standard, unprivileged, broadcastable transaction types.

### Recommendation
Move from purely cycle-granular vote/reward accounting to a duration- or block-weighted accrual model (e.g., weight `Vi` contributions by the fraction of the cycle a vote was actually active, or snapshot vote power at fixed intra-cycle checkpoints with minimum holding-time requirements before a vote becomes reward-eligible for a cycle). At minimum, delay reward eligibility for votes cast/changed near a maintenance boundary by one additional cycle to prevent boundary-straddling flash-voting from capturing full-cycle rewards.

### Proof of Concept
Conceptual sequence (cannot be dynamically executed here, but derivable directly from the code paths cited above):
1. Attacker freezes balance and casts `VoteWitnessContract` for witness W in the last block before `doMaintenance()` runs for cycle N→N+1. `countVoteAccount()` records this via `votesStore` new-votes, and `mortgageService.withdrawReward()` sets `beginCycle = N+1`, `endCycle = N+2` for the attacker (see `MortgageService.withdrawReward`, lines 89-134).
2. `doMaintenance()` runs: `accumulateWitnessVi(N, W, oldVoteCount)` uses the pre-vote weight (fine), then `countVote()` folds in the attacker's new vote into `witnessCapsule.voteCount`, and `setWitnessVote(N+1, W, newVoteCount)` locks in the attacker's vote as active for the entirety of cycle N+1 (lines 96-162 of `MaintenanceManager`).
3. In the very first block of cycle N+1, attacker calls `UnfreezeBalanceV2Actuator`/`clearVote`, which withdraws pending reward and removes the vote from `votesList`, but this removal is only reflected in `votesStore` and will not affect `witnessCapsule.voteCount` until the *next* maintenance (N+1→N+2).
4. When cycle N+1 ends and Vi(N+1) is accumulated using the vote weight that still includes the attacker's vote for the whole cycle N+1, then attacker calls `queryReward`/`withdrawReward`; `computeReward(N+1, N+2, account)` pays the attacker the full cycle N+1 share of witness W's rewards (`MortgageService.computeReward`, lines 199-230), despite the attacker having genuinely held the vote for well under a full cycle.

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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L103-127)
```java
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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L165-195)
```java
  private Map<ByteString, Long> countVote(VotesStore votesStore) {
    final Map<ByteString, Long> countWitness = Maps.newHashMap();
    Iterator<Entry<byte[], VotesCapsule>> dbIterator = votesStore.iterator();
    long sizeCount = 0;
    while (dbIterator.hasNext()) {
      Entry<byte[], VotesCapsule> next = dbIterator.next();
      VotesCapsule votes = next.getValue();
      votes.getOldVotes().forEach(vote -> {
        ByteString voteAddress = vote.getVoteAddress();
        long voteCount = vote.getVoteCount();
        if (countWitness.containsKey(voteAddress)) {
          countWitness.put(voteAddress, countWitness.get(voteAddress) - voteCount);
        } else {
          countWitness.put(voteAddress, -voteCount);
        }
      });
      votes.getNewVotes().forEach(vote -> {
        ByteString voteAddress = vote.getVoteAddress();
        long voteCount = vote.getVoteCount();
        if (countWitness.containsKey(voteAddress)) {
          countWitness.put(voteAddress, countWitness.get(voteAddress) + voteCount);
        } else {
          countWitness.put(voteAddress, voteCount);
        }
      });
      sizeCount++;
      votesStore.delete(next.getKey());
    }
    logger.info("There is {} new votes in this epoch", sizeCount);
    return countWitness;
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

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L152-191)
```java
  private void countVoteAccount(VoteWitnessContract voteContract) {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    VotesStore votesStore = chainBaseManager.getVotesStore();
    MortgageService mortgageService = chainBaseManager.getMortgageService();
    byte[] ownerAddress = voteContract.getOwnerAddress().toByteArray();

    VotesCapsule votesCapsule;

    //
    mortgageService.withdrawReward(ownerAddress);

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);

    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    if (dynamicStore.supportAllowNewResourceModel()
        && accountCapsule.oldTronPowerIsNotInitialized()) {
      accountCapsule.initializeOldTronPower();
    }

    if (!votesStore.has(ownerAddress)) {
      votesCapsule = new VotesCapsule(voteContract.getOwnerAddress(),
          accountCapsule.getVotesList());
    } else {
      votesCapsule = votesStore.get(ownerAddress);
    }

    accountCapsule.clearVotes();
    votesCapsule.clearNewVotes();

    voteContract.getVotesList().forEach(vote -> {
      logger.debug("countVoteAccount, address[{}]",
          ByteArray.toHexString(vote.getVoteAddress().toByteArray()));

      votesCapsule.addNewVotes(vote.getVoteAddress(), vote.getVoteCount());
      accountCapsule.addVotes(vote.getVoteAddress(), vote.getVoteCount());
    });

    accountStore.put(accountCapsule.createDbKey(), accountCapsule);
    votesStore.put(ownerAddress, votesCapsule);
  }
```
