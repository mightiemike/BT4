### Title
Unbounded `VotesStore` Growth Causes Increasingly Expensive Synchronous Maintenance Loop, Risking Block-Production Stalls - (File: `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java`)

### Summary
The external report describes gas-intensive nested loops (`_requestExitsBasedOnRedeemDemandAfterRebalancings`, `_pickNextValidatorsToDepositFromActiveOperators`) whose cost scales with unbounded, attacker-influenceable state, to the point that a critical operation becomes uncallable. The closest reachable analog in java-tron is `MaintenanceManager.doMaintenance()` → `countVote()`, which iterates the *entire* `VotesStore` synchronously on every maintenance cycle as part of block processing. Any unprivileged TRX holder can cheaply grow this store by casting witness votes from many different accounts, increasing the fixed, mandatory per-cycle workload every full node must perform inline while producing/validating blocks.

### Finding Description
`VoteWitnessActuator.countVoteAccount` persists one `VotesCapsule` entry per unique voting account address into `VotesStore`, containing up to `MAX_VOTE_NUMBER` old/new votes each: [1](#0-0) 

This store is only cleared for accounts that voted during the epoch, and clearing happens exclusively inside `MaintenanceManager.countVote()`, which is invoked from `doMaintenance()`: [2](#0-1) 

`doMaintenance()` itself is called synchronously from `applyBlock()` whenever the maintenance time boundary is crossed — i.e., it is embedded directly in the block-application critical path of every node, with no way to split it across multiple calls or throttle it based on caller input, unlike the gas-metered Solidity function in the report: [3](#0-2) [4](#0-3) 

The cost of `countVote()` (and downstream witness reward/vote-count updates in `doMaintenance`) is O(number of distinct voting accounts × votes per account), both of which are entirely controlled by unprivileged users at negligible cost (many accounts each casting `VoteWitnessContract` transactions with up to `MAX_VOTE_NUMBER` entries). This mirrors the reported bug class: a mandatory, unavoidable computation whose gas/CPU cost scales with state that ordinary users can inflate, executed as an atomic, non-interruptible unit rather than being split into caller-bounded chunks (as the report's remediation — `demandValidatorExits`/`requestValidatorExits` — did for the Solidity code).

### Impact Explanation
Because `doMaintenance()` runs synchronously inside block application on every witness/full node, an inflated `VotesStore` increases the wall-clock time required to process the maintenance block. Since TRON has fixed ~3-second block production slots and DPoS scheduling, sufficiently large per-cycle computation can cause a witness to miss its block slot, delay block propagation, or (in the worst case across many nodes) create timing divergence between nodes with different processing capacity — an availability/liveness impact class (block-production stall), which corresponds to the "invalid-state/divergence/halt" category of accepted impacts.

### Likelihood Explanation
Likelihood is bounded by economics: creating each `VotesStore` entry requires an on-chain `VoteWitnessContract` transaction from a distinct account holding TRX (for TRON Power) and paying the transaction's bandwidth/energy cost, which are far cheaper than the multi-step validator lifecycle abused in the original Solidity report. There is no cap on total number of accounts in `VotesStore`, and existing entries persist across cycles until the account votes again or an epoch specifically clears them, so growth is cumulative and directional rather than self-limiting.

### Recommendation
- Bound the amount of work `doMaintenance()`/`countVote()` performs per maintenance cycle (e.g., paginate/checkpoint processing of `VotesStore` across multiple blocks, or add a periodic cleanup pass that prunes/consolidates stale entries).
- Consider capping the number of live `VotesStore` entries or expiring entries for accounts with no active TRON Power to prevent unbounded accumulation from cheap distinct-account voting.
- Add monitoring/metrics on `VotesStore` size and maintenance duration, and add a regression/load test that measures `doMaintenance()` cost against a `VotesStore` sized at a realistic worst case (e.g., hundreds of thousands of distinct voter accounts).

### Proof of Concept
1. Create N distinct accounts, each funded with the minimum TRX needed to obtain TRON Power.
2. From each account, broadcast a `VoteWitnessContract` transaction voting for one or more witnesses (up to `MAX_VOTE_NUMBER` votes), causing `VoteWitnessActuator.countVoteAccount` to insert a `VotesCapsule` into `VotesStore` for that account.
3. Repeat for as many accounts as economically feasible (cost is only the vote transactions' bandwidth/energy fees, no burn of TRON Power required beyond minimal amounts).
4. Observe that the next maintenance-boundary block triggers `MaintenanceManager.doMaintenance()` → `countVote()`, iterating all N `VotesCapsule` entries and their vote lists synchronously as part of block application; measure the increase in block-processing time for the maintenance block as N grows, and compare against the fixed block interval to determine whether a slot-miss threshold is reachable.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L170-191)
```java

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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L89-163)
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

    if (dynamicPropertiesStore.allowChangeDelegation()) {
      long nextCycle = dynamicPropertiesStore.getCurrentCycleNumber() + 1;
      dynamicPropertiesStore.saveCurrentCycleNumber(nextCycle);
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.setBrokerage(nextCycle, witness.createDbKey(),
            delegationStore.getBrokerage(witness.createDbKey()));
        delegationStore.setWitnessVote(nextCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }
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
