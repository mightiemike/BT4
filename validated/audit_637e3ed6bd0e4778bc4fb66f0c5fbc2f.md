### Title
Unbounded full-store iteration in `MaintenanceManager.doMaintenance()` executed synchronously on the block-processing (EndBlocker-equivalent) path can slow or halt block production - ([File: consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java])

### Summary
The reported Allora bug is that `SafeApplyFuncOnAllActiveEpochEndingTopics`, invoked from an ABCI `EndBlocker`-equivalent hook, iterates over an unbounded/incorrectly-bounded set of on-chain items and can slow down or halt block production. The java-tron analog is `MaintenanceManager.doMaintenance()` (and its helper `countVote()`), which is invoked unconditionally, with **no page limit, no item cap, and no cadence check**, from the block-processing critical path every time a maintenance boundary is crossed.

### Finding Description
`Manager.processBlock()` — the function that plays the role of java-tron's per-block "end of block" processing (analogous to Cosmos SDK's `EndBlocker`) — checks whether the current block crosses a maintenance-time boundary and, if so, synchronously calls `proposalController.processProposals()` and then `consensus.applyBlock(block)`: [1](#0-0) 

`consensus.applyBlock(block)` routes into `MaintenanceManager.applyBlock()`, which — when the maintenance-time flag is set — calls `doMaintenance()` unconditionally: [2](#0-1) 

`doMaintenance()` then:
1. Iterates over **all** witnesses (`consensusDelegate.getAllWitnesses()`) at least twice, with no page limit or cap: [3](#0-2) 
2. Calls `countVote(votesStore)`, which iterates over the **entire `VotesStore`** — one entry per account that has ever voted — with a plain `while (dbIterator.hasNext())` loop and no pagination, no item limit, and no bound at all: [4](#0-3) 
3. Iterates again over all witnesses to update vote counts and job flags: [5](#0-4) 

Unlike the Allora `SafeApplyFuncOnAllActiveEpochEndingTopics` function, which at least attempts (imperfectly) to bound iteration via `topicPageLimit`/`maxTopicPages`, this java-tron code has **no bound whatsoever**: it processes the complete `VotesStore` and complete witness set every maintenance cycle, synchronously, inside the same call stack as block validation/application. As the number of TRC accounts that have ever cast a vote (`VotesStore` entries) grows with chain usage, this loop's cost grows unboundedly and is paid entirely within the time budget for producing/applying one block.

### Impact Explanation
Because `doMaintenance()` executes synchronously inside `processBlock()` (the equivalent of the vulnerable `EndBlocker` code path in the referenced report), an unbounded/slow iteration here directly extends per-block processing time at every maintenance-time boundary (default cadence periodically, e.g., every 6 hours by default in TRON's maintenance interval). This can:
- Delay block production/witness scheduling, causing the node to miss its slot,
- Cause other nodes (which must independently execute the identical deterministic logic) to also fall behind, and
- In the worst case, if `VotesStore` grows large enough (adversary-driven vote transactions from many distinct accounts), cause a chain-wide slowdown or stall at every maintenance boundary — matching the "DoS via protocol implementation" impact class.

This is reachable by any user: submitting `VoteWitnessContract` transactions from many distinct accounts inflates `VotesStore` size (each voter address is a `VotesStore` key), directly increasing the cost of the unbounded `countVote()` scan at the next maintenance cycle.

### Likelihood Explanation
Likelihood is moderate-to-low in practice because:
- The cost scales with the number of *distinct account addresses that have outstanding vote changes* in the current epoch (entries get deleted after processing), not the entire historical account set, which bounds growth somewhat.
- No privileged access is required — any account holding TRX can submit a `VoteWitnessContract`, and an attacker willing to fund many accounts could inflate this store, but doing so at scale requires paying transaction fees for each additional voter address.
- Similarly, `getAllWitnesses()` iterations are bounded by the (comparatively small, capacity-limited) number of registered witnesses, so that part is less concerning; the `countVote()` scan over `VotesStore` is the more significant unbounded factor.

### Recommendation
- Add explicit metrics/logging around `doMaintenance()` and `countVote()` execution time (similar to what already exists for `processTransaction`) to detect slow maintenance cycles in production.
- Bound and/or paginate the `VotesStore` scan in `countVote()`, or maintain running vote-delta aggregates incrementally as votes are cast (in `VoteWitnessActuator`) rather than recomputing the full delta from a linear scan at maintenance time.
- Consider moving heavy maintenance-time aggregation off the synchronous block-application critical path, or explicitly budget/time-limit it and defer overflow work to subsequent blocks, consistent with the original report's recommendation to bound ABCI-adjacent handlers so they scale with usage growth.

### Proof of Concept
1. An attacker (or organic usage growth) creates a large number of distinct funded accounts and has each cast a `VoteWitnessContract` transaction within the same maintenance epoch, populating `VotesStore` with one entry per voting address: [6](#0-5) 
2. At the next maintenance-time boundary, `Manager.processBlock()` triggers `consensus.applyBlock(block)` synchronously while applying the block: [7](#0-6) 
3. `MaintenanceManager.doMaintenance()` performs a full, unbounded scan of `VotesStore` via `countVote()`, with cost proportional to the number of distinct voter entries created in step 1: [4](#0-3) 
4. This synchronous, unbounded work delays completion of block application for that block, directly analogous to the reported "slow ABCI method" impact of delayed/halted block production.

### Citations

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1917-1927)
```java
    payReward(block);

    boolean flag = chainBaseManager.getDynamicPropertiesStore().getNextMaintenanceTime()
        <= block.getTimeStamp();
    if (flag) {
      proposalController.processProposals();
    }

    if (!consensus.applyBlock(block)) {
      throw new BadBlockException("consensus apply block failed");
    }
```

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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L96-152)
```java
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

      dposService.updateWitness(newWitnessAddressList);

      incentiveManager.reward(newWitnessAddressList);

      List<ByteString> newWits = consensusDelegate.getActiveWitnesses();
      if (!CollectionUtils.isEqualCollection(currentWits, newWits)) {
        currentWits.forEach(address -> {
          WitnessCapsule witnessCapsule = consensusDelegate.getWitness(address.toByteArray());
          witnessCapsule.setIsJobs(false);
          consensusDelegate.saveWitness(witnessCapsule);
        });
        newWits.forEach(address -> {
          WitnessCapsule witnessCapsule = consensusDelegate.getWitness(address.toByteArray());
          witnessCapsule.setIsJobs(true);
          consensusDelegate.saveWitness(witnessCapsule);
        });

        SRMetrics.recordSrSetChange(currentWits, newWits);
      }

      logger.info("Update witness success. \nbefore: {} \nafter: {}",
          getAddressStringList(currentWits),
          getAddressStringList(newWits));
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
