### Title
Denial-of-Service via Unbounded Vote Queue Dilution in `MaintenanceManager.doMaintenance()` - (File: consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java)

### Summary
`MaintenanceManager.doMaintenance()` iterates over the entire `VotesStore` on every maintenance cycle to tally witness votes, and this store is populated by unprivileged `VoteWitnessContract` transactions with no minimum vote/stake threshold and no cap on the number of distinct voter entries. An attacker can cheaply create many low-cost accounts, freeze a minimal balance to obtain non-zero TRON Power, and cast votes, bloating `VotesStore` and increasing the cost of the unbounded loop that every full node must execute synchronously while applying blocks.

### Finding Description
`doMaintenance()` calls the private `countVote(VotesStore votesStore)` method, which walks a full DB iterator over `VotesStore` with no bound on the number of entries processed: [1](#0-0) 

This is invoked unconditionally from `doMaintenance()`, which is triggered as part of the deterministic consensus/block-application flow via `DposService`: [2](#0-1) 

Each entry in `VotesStore` is created/updated by `VoteWitnessActuator.countVoteAccount()`/`VoteWitnessProcessor.execute()`, which only requires `voteCount > 0` and that the resulting vote weight not exceed the caller's TRON Power — there is no minimum vote amount enforced: [3](#0-2) [4](#0-3) 

TRON Power is obtained by freezing balance via `FreezeBalanceV2Actuator`, whose `execute()` path accepts an attacker-chosen `frozenBalance` without an explicit floor beyond it being a positive value deducted from the account balance: [5](#0-4) 

This mirrors the reported bug class exactly: unbounded, low-cost, user-controlled entries feed an iterative process (`executeAllCommitments()` in the original report vs. `countVote()`/`doMaintenance()` here) that a time/resource-constrained actor (the pool keeper vs. every full node during block application) must fully process.

### Impact Explanation
`doMaintenance()` runs on the block-production/consensus critical path via `DposService`, not as an isolated off-chain job. If the cost of iterating `VotesStore` grows large enough (many thousands/millions of distinct voter accounts), the maintenance step could materially slow block application on every full node simultaneously, risking missed block-production slots or a de-facto network-wide slowdown/halt around maintenance-cycle boundaries — a more severe variant of the "keeper misses upkeep" impact described in the original report, since here it affects the entire network's block cadence rather than one external actor.

### Likelihood Explanation
Exploitation requires the attacker to fund many accounts and freeze minimal TRX in each to gain non-zero TRON Power, then submit `VoteWitnessContract` transactions. Each such transaction costs bandwidth/energy and the freeze itself locks TRX (recoverable later), which raises the cost above zero, but there is no explicit protocol-level minimum stake or per-cycle cap that would make a large-scale dilution attack prohibitively expensive, unlike the recommendation in the original report calling for exactly such minimums. The exact economic threshold at which this becomes practically exploitable, and the actual wall-clock cost of `countVote()` at scale, was not fully quantified from static review alone.

### Recommendation
Introduce a minimum TRON Power/vote-count threshold in `VoteWitnessActuator`/`VoteWitnessProcessor` validation (analogous to a minimum commit size), and/or bound `MaintenanceManager.countVote()` to process at most a fixed number of `VotesStore` entries per cycle (with overflow carried to a subsequent cycle), similar to the two mitigations recommended in the original report: minimum commit size and a cap on iteration size per interval.

### Proof of Concept
1. Attacker programmatically creates N accounts (e.g., tens of thousands), funding each with a minimal TRX balance.
2. For each account, submit a `FreezeBalanceV2Contract` transaction freezing a minimal `frozenBalance` (e.g., 1 TRX) for `BANDWIDTH`, per `FreezeBalanceV2Actuator.execute()` (no enforced minimum beyond a positive value) — [6](#0-5) .
3. From each account, submit a `VoteWitnessContract` with `voteCount = 1` for any active witness, which passes validation since the only checks are `voteCount > 0` and total vote weight ≤ TRON Power — [3](#0-2) .
4. This creates N entries in `VotesStore`, one per voting account.
5. At the next maintenance cycle, `doMaintenance()` calls `countVote(votesStore)`, which iterates all N entries via `dbIterator` on the full node's critical block-processing path — [1](#0-0) , increasing processing time for that block proportionally to N across every full node in the network.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L89-121)
```java
    if (contract.getVotesCount() == 0) {
      throw new ContractValidateException(
          "VoteNumber must more than 0");
    }
    int maxVoteNumber = MAX_VOTE_NUMBER;
    if (contract.getVotesCount() > maxVoteNumber) {
      throw new ContractValidateException(
          "VoteNumber more than maxVoteNumber " + maxVoteNumber);
    }
    try {
      Iterator<Vote> iterator = contract.getVotesList().iterator();
      Long sum = 0L;
      while (iterator.hasNext()) {
        Vote vote = iterator.next();
        byte[] witnessCandidate = vote.getVoteAddress().toByteArray();
        if (!DecodeUtil.addressValid(witnessCandidate)) {
          throw new ContractValidateException("Invalid vote address!");
        }
        long voteCount = vote.getVoteCount();
        if (voteCount <= 0) {
          throw new ContractValidateException("vote count must be greater than 0");
        }
        String readableWitnessAddress = StringUtil.createReadableString(vote.getVoteAddress());
        if (!accountStore.has(witnessCandidate)) {
          throw new ContractValidateException(
              ACCOUNT_EXCEPTION_STR + readableWitnessAddress + NOT_EXIST_STR);
        }
        if (!witnessStore.has(witnessCandidate)) {
          throw new ContractValidateException(
              WITNESS_EXCEPTION_STR + readableWitnessAddress + NOT_EXIST_STR);
        }
        sum = LongMath.checkedAdd(sum, vote.getVoteCount());
      }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L55-86)
```java
    Iterator<Protocol.Vote> iterator = param.getVotes().iterator();
    try {
      long sum = 0;
      while (iterator.hasNext()) {
        Protocol.Vote vote = iterator.next();

        byte[] witnessAddress = vote.getVoteAddress().toByteArray();
        /*
          Already covered while doing maintenance in MaintenanceManager.java, for tvm performance,
          we remove the account check
         */
//        if (repo.getAccount(witnessAddress) == null) {
//          throw new ContractValidateException(
//              ACCOUNT_EXCEPTION_STR + StringUtil.encode58Check(witnessAddress) + NOT_EXIST_STR);
//        }
        if (repo.getWitness(witnessAddress) == null) {
          throw new ContractExeException(
              WITNESS_EXCEPTION_STR + StringUtil.encode58Check(witnessAddress) + NOT_EXIST_STR);
        }

        long voteCount = vote.getVoteCount();
        if (voteCount < 0) {
          throw new ContractExeException("Vote count must not be less than 0");
        } else if (voteCount == 0) {
          iterator.remove();
        } else {
          sum = LongMath.checkedAdd(sum, voteCount);
          // merge vote for same witness
          voteMap.put(vote.getVoteAddress(),
              LongMath.checkedAdd(voteMap.getOrDefault(vote.getVoteAddress(), 0L), voteCount));
        }
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L57-70)
```java
    long frozenBalance = freezeBalanceV2Contract.getFrozenBalance();
    long newBalance = accountCapsule.getBalance() - frozenBalance;

    switch (freezeBalanceV2Contract.getResource()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(frozenBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        dynamicStore.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(frozenBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
```
