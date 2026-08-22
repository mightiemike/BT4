### Title
Unbounded VotesStore iteration in `MaintenanceManager.doMaintenance()` allows attacker-driven DoS of consensus-critical epoch processing - (File: `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java`)

### Summary
`MaintenanceManager.doMaintenance()` is the java-tron analog of `Voter::finalize()`: it runs once per maintenance cycle (epoch) as part of block application and must process every pending voter record before the cycle can complete. Its helper `countVote(VotesStore)` iterates the *entire* `VotesStore` with a single unbounded `while (dbIterator.hasNext())` loop and deletes every entry inline, with no batch/size cap, mirroring the missing "batch argument" flaw in `Voter::_processPendingRemovals()`.

### Finding Description
Any account can grow `VotesStore` by calling `VoteWitnessContract` (handled by `VoteWitnessActuator.countVoteAccount()`), which unconditionally does `votesStore.put(ownerAddress, votesCapsule)` for the calling account [1](#0-0) . The only real precondition is holding enough TRON Power (frozen balance) to cover the requested vote count, checked in `validate()` [2](#0-1)  — a cheap, unprivileged precondition analogous to the original bug's "attacker calls `vote()` with a wei balance."

Every maintenance/epoch cycle, `MaintenanceManager.doMaintenance()` calls `countVote(votesStore)`, which walks the full `VotesStore` via a DB iterator and, crucially, deletes each record from the store as part of the same unbounded loop: [3](#0-2) 

Unlike the `Voter::_processPendingRemovals()` bug where a Solidity `for` loop runs out of *gas*, here the analog resource is wall-clock/CPU time and heap during block application: `doMaintenance()` is invoked synchronously from `applyBlock()` [4](#0-3)  as every full node processes the block that crosses the maintenance boundary. There is no limit on the number of distinct voter addresses (i.e., `VotesStore` keys) that can accumulate before the next maintenance triggers, and no chunking/batching parameter in `countVote()` or `doMaintenance()` to cap per-call work — exactly the missing "batch argument" defect described in the source report.

The read-only `Wallet.getPaginatedNowWitnessList()` path contains a near-identical unbounded `countVote(VotesStore)` reimplementation [5](#0-4) , which is a second RPC-reachable instance of the same unbatched full-store scan, though that path does not perform destructive deletes.

### Impact Explanation
If an attacker registers a large number of accounts, freezes the minimal amount of balance required for TRON Power, and casts votes from each (a cheap, unprivileged, bandwidth-cost-only operation), `VotesStore` accumulates one entry per unique voter. At the next maintenance boundary, every full node/witness must execute `countVote()` over the entire store synchronously inside block application. With enough spammed voter accounts, this can materially slow down or stall block processing across the network at the maintenance boundary — a consensus-wide availability degradation, not an isolated node issue, since `doMaintenance()` runs identically on every participating node. This matches the "impossible to finalize epochs, rewards stuck" impact class from the source report, translated to java-tron's block-processing/consensus context.

### Likelihood Explanation
Likelihood is moderate-to-high given the low cost of the precondition (minimal freeze + one `VoteWitnessContract` transaction per attacker-controlled account, all normal unprivileged flows) and the fact that vote counting/deletion during maintenance is unconditional and already known internally to be O(n) in the number of active voters, with `sizeCount` explicitly logged [3](#0-2) , i.e., the codebase already tracks this count without bounding it.

### Recommendation
Introduce a bounded/batched processing model for `VotesStore` traversal in `countVote()`/`doMaintenance()`, e.g., cap the number of `VotesCapsule` entries processed and deleted per maintenance invocation, carry over remaining entries to a subsequent invocation, or otherwise decouple vote tallying from block-critical maintenance so a single block application cannot be forced into unbounded work by a large but cheap set of voter accounts. Apply the same treatment to the equivalent scan in `Wallet.getPaginatedNowWitnessList()`.

### Proof of Concept
1. Attacker creates N accounts, freezes the minimum balance required to obtain non-zero TRON Power for each (satisfies `VoteWitnessActuator.validate()`).
2. Attacker submits a `VoteWitnessContract` transaction from each account, causing `VotesStore` to grow by N entries via `VoteWitnessActuator.countVoteAccount()`.
3. At the next maintenance boundary, `MaintenanceManager.applyBlock()` triggers `doMaintenance()` → `countVote(votesStore)`, which must iterate and delete all N entries synchronously while the node applies the block.
4. For sufficiently large N (cheap to generate given only bandwidth cost per vote transaction), this materially delays or DoSes block application for every node processing that maintenance-boundary block, analogous to the reported `Voter::finalize()` OOG DoS.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L123-143)
```java
      AccountCapsule accountCapsule = accountStore.get(ownerAddress);
      if (accountCapsule == null) {
        throw new ContractValidateException(
            ACCOUNT_EXCEPTION_STR + readableOwnerAddress + NOT_EXIST_STR);
      }

      long tronPower;
      DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
      if (dynamicStore.supportAllowNewResourceModel()) {
        tronPower = accountCapsule.getAllTronPower();
      } else {
        tronPower = accountCapsule.getTronPower();
      }

      sum = LongMath
          .checkedMultiply(sum, TRX_PRECISION); //trx -> drop. The vote count is based on TRX
      if (sum > tronPower) {
        throw new ContractValidateException(
            "The total number of votes[" + sum + "] is greater than the tronPower[" + tronPower
                + "]");
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

**File:** framework/src/main/java/org/tron/core/Wallet.java (L842-872)
```java
  private Map<ByteString, Long> countVote(VotesStore votesStore) {
    // Initialize a result map to store vote changes for each witness
    Map<ByteString, Long> countWitness = Maps.newHashMap();

    // VotesStore is a key-value store, where the key is the address of the voter
    Iterator<Entry<byte[], VotesCapsule>> dbIterator = votesStore.iterator();

    while (dbIterator.hasNext()) {
      Entry<byte[], VotesCapsule> next = dbIterator.next();
      VotesCapsule votes = next.getValue();

      /**
       * VotesCapsule contains two lists:
       * - Old votes: Last votes from the previous epoch, updated in maintenance period
       * - New votes: Latest votes in current epoch, updated after each vote transaction
       */
      votes.getOldVotes().forEach(vote -> {
        ByteString voteAddress = vote.getVoteAddress();
        long voteCount = vote.getVoteCount();
        countWitness.put(voteAddress,
            countWitness.getOrDefault(voteAddress, 0L) - voteCount);
      });
      votes.getNewVotes().forEach(vote -> {
        ByteString voteAddress = vote.getVoteAddress();
        long voteCount = vote.getVoteCount();
        countWitness.put(voteAddress,
            countWitness.getOrDefault(voteAddress, 0L) + voteCount);
      });
    }
    return countWitness;
  }
```
