### Title
Unbounded, permissionlessly-grown Witness set is iterated multiple times in unmetered consensus block processing (`doMaintenance`), enabling a cheap chain-halt / block-time-overrun vector - ([File: consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java])

### Summary
Any account can permissionlessly create a new Witness by paying a single fixed fee (`getAccountUpgradeCost()`), with no cost that scales with the number of witnesses already registered. [1](#0-0)  All witnesses ever created remain permanently in `WitnessStore` (there is no pruning or cap on the underlying key space, only on the *active* set used for block production), so the total witness count is attacker-controlled and unbounded. [2](#0-1)  Every maintenance cycle (deterministic, roughly every 6 hours, triggered by block time crossing `getNextMaintenanceTime()`), `MaintenanceManager.doMaintenance()` runs several full linear (and some log-linear) scans over *all* witnesses and *all* votes cast in the epoch, entirely inside consensus-critical, unmetered block-processing code — the exact bug class described in the report (unbounded iteration over a permissionlessly-created, fee-protected-but-not-scaling collection, executed in `BeginBlock`-equivalent logic). [3](#0-2) 

### Finding Description
`doMaintenance()` is invoked from `MaintenanceManager.applyBlock()`, which is called from `DposService.applyBlock()`, which in turn is called synchronously from `Manager.processBlock()` for every block whose timestamp crosses the next maintenance boundary. [4](#0-3) [5](#0-4)  This is functionally the java-tron analog of Cosmos SDK's `BeginBlock`: deterministic, consensus-critical logic that every full node/witness must execute while producing/validating that block, with no gas metering (java-tron's account-permission/proposal/witness logic runs in plain Java, unlike EVM contract execution).

Inside `doMaintenance()`:
- `consensusDelegate.getAllWitnesses().forEach(...)` is called (up to 3 separate times in the method) to accumulate `Vi`, build a new witness address list, and set brokerage/vote for the next cycle — each an O(N) scan over every witness ever registered. [6](#0-5) [7](#0-6) 
- `countVote(votesStore)` iterates the entire `VotesStore` (every account that voted this epoch), deleting each entry as it goes. [8](#0-7) 
- `dposService.updateWitness(list)` sorts the full candidate witness list (`sortWitness`, O(N log N)). [9](#0-8) 
- `incentiveManager.reward(newWitnessAddressList)` and the `currentWits.forEach/newWits.forEach` block add further O(N) work. [10](#0-9) [11](#0-10) 

The registration side (`WitnessCreateActuator`) charges only a single flat fee (`dynamicStore.getAccountUpgradeCost()`, deducted once) per witness creation, with no term that grows with the current size of `WitnessStore` — mirroring exactly the root cause identified in the report ("there is a fee for plan creation... it is not high enough to protect against this attack... Add a scaling gas cost to plan creation"). [12](#0-11)  An attacker who funds N accounts each above `getAccountUpgradeCost()` can register N witnesses over time (across many transactions/blocks, since a `WitnessCreateContract` is one-per-account, unbounded across distinct accounts) and force every node's next maintenance-boundary block to perform multiple O(N) or O(N log N) scans of the full witness/vote stores synchronously inside block processing.

### Impact Explanation
Because `doMaintenance()` executes unconditionally and synchronously as part of processing the block that crosses the maintenance boundary, an attacker-inflated witness/vote set directly increases the wall-clock time every witness needs to process that specific block. Since java-tron block production has a bounded production window (block interval), if this maintenance-triggered processing exceeds that window, block production/validation will be delayed or fail across the network for that cycle, i.e., a network-wide slowdown/stall recurring every maintenance interval (~6h) once enough witnesses exist. This matches the reported impact class of "invalid-state/divergence/halt" and "underpriced-public-work" — permissionless, fee-gated but non-scaling work that is executed unmetered inside consensus-critical block processing.

### Likelihood Explanation
Likelihood is constrained relative to the original Cosmos report because:
- Witness creation costs a fixed TRX amount per witness and requires a distinct, previously-non-witness account per registration (`witnessStore.has(ownerAddress)` check), so scaling to tens of thousands of witnesses requires funding and submitting that many separate accounts/transactions rather than a single call creating many entries at once as in the Rewards Plan POC. [13](#0-12) 
- This raises the attack cost and time compared to the original one-call-per-block flood in the report, but the underlying flaw (no scaling cost, unbounded permanent storage, full unmetered iteration in deterministic block logic) is structurally identical.

### Recommendation
- Scale the witness-creation fee (or apply an explicit gas/resource charge) proportionally to the current number of witnesses in `WitnessStore`, analogous to the report's recommendation (`ctx.GasMeter().ConsumeGas(BaseFee * len(existingWitnesses))`), inside `WitnessCreateActuator.calcFee()`/`createWitness()`. [14](#0-13) 
- Alternatively (or additionally), cap the total number of storable witnesses, or restructure `doMaintenance()` to avoid full O(N) scans over the entire historical witness set every cycle (e.g., maintain running aggregates instead of recomputing from scratch in `MaintenanceManager.doMaintenance()`/`countVote()`). [15](#0-14) 

### Proof of Concept
Conceptual reproduction path (adapting the report's POC pattern to java-tron):
1. Fund N distinct accounts each with balance ≥ `getAccountUpgradeCost()`.
2. Submit N `WitnessCreateContract` transactions (one per account) across however many blocks are needed; each succeeds since `witnessStore.has(ownerAddress)` only rejects re-registration of the same address, not a global cap. [16](#0-15) 
3. Optionally submit `VoteWitnessContract` transactions from many accounts to also inflate `VotesStore`, amplifying `countVote()` cost. [8](#0-7) 
4. Wait for the block whose timestamp crosses `getNextMaintenanceTime()`; measure the time `Manager.processBlock()` spends inside `consensus.applyBlock()` → `MaintenanceManager.doMaintenance()` as N grows, analogous to the reported `AllocateRewards` timing measurement. [5](#0-4) [4](#0-3) 

I was not able to independently verify the exact production block-interval enforcement/timeout thresholds or confirm empirically at what witness count this scan would exceed the block production window — that would require running the above scenario against a live/test node, which is outside what static code inspection can establish with certainty.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java (L98-149)
```java
    if (witnessStore.has(ownerAddress)) {
      throw new ContractValidateException(
          WITNESS_EXCEPTION_STR + readableOwnerAddress + "] has existed");
    }

    if (accountCapsule.getBalance() < dynamicStore
        .getAccountUpgradeCost()) {
      throw new ContractValidateException("balance < AccountUpgradeCost");
    }

    return true;
  }

  @Override
  public ByteString getOwnerAddress() throws InvalidProtocolBufferException {
    return any.unpack(WitnessCreateContract.class).getOwnerAddress();
  }

  @Override
  public long calcFee() {
    return chainBaseManager.getDynamicPropertiesStore().getAccountUpgradeCost();
  }

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
    adjustBalance(accountStore, witnessCreateContract.getOwnerAddress().toByteArray(), -cost);
    if (dynamicStore.supportBlackHoleOptimization()) {
      dynamicStore.burnTrx(cost);
    } else {
      adjustBalance(accountStore, accountStore.getBlackhole(), +cost);
    }
    dynamicStore.addTotalCreateWitnessCost(cost);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/WitnessStore.java (L29-56)
```java
  /**
   * get all witnesses.
   */
  public List<WitnessCapsule> getAllWitnesses() {
    return Streams.stream(iterator())
        .map(Entry::getValue)
        .collect(Collectors.toList());
  }

  @Override
  public WitnessCapsule get(byte[] key) {
    byte[] value = revokingDB.getUnchecked(key);
    return ArrayUtils.isEmpty(value) ? null : new WitnessCapsule(value);
  }

  public List<WitnessCapsule> getWitnessStandby(boolean isSortOpt) {
    List<WitnessCapsule> ret;
    List<WitnessCapsule> all = getAllWitnesses();
    sortWitnesses(all, isSortOpt);
    if (all.size() > Parameter.ChainConstant.WITNESS_STANDBY_LENGTH) {
      ret = new ArrayList<>(all.subList(0, Parameter.ChainConstant.WITNESS_STANDBY_LENGTH));
    } else {
      ret = new ArrayList<>(all);
    }
    // trim voteCount = 0
    ret.removeIf(w -> w.getVoteCount() < 1);
    return ret;
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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L89-195)
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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1919-1931)
```java
    boolean flag = chainBaseManager.getDynamicPropertiesStore().getNextMaintenanceTime()
        <= block.getTimeStamp();
    if (flag) {
      proposalController.processProposals();
    }

    if (!consensus.applyBlock(block)) {
      throw new BadBlockException("consensus apply block failed");
    }

    if (flag) {
      chainBaseManager.getForkController().reset();
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

**File:** consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java (L20-43)
```java
  public void reward(List<ByteString> witnesses) {
    if (consensusDelegate.allowChangeDelegation()) {
      return;
    }
    if (witnesses.size() > WITNESS_STANDBY_LENGTH) {
      witnesses = witnesses.subList(0, WITNESS_STANDBY_LENGTH);
    }
    long voteSum = 0;
    for (ByteString witness : witnesses) {
      voteSum += consensusDelegate.getWitness(witness.toByteArray()).getVoteCount();
    }
    if (voteSum <= 0) {
      return;
    }
    long totalPay = consensusDelegate.getWitnessStandbyAllowance();
    for (ByteString witness : witnesses) {
      byte[] address = witness.toByteArray();
      long pay = (long) (consensusDelegate.getWitness(address).getVoteCount() * ((double) totalPay
          / voteSum));
      AccountCapsule accountCapsule = consensusDelegate.getAccount(address);
      accountCapsule.setAllowance(accountCapsule.getAllowance() + pay);
      consensusDelegate.saveAccount(accountCapsule);
    }
  }
```
