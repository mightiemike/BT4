This confirms the analog: `WitnessCreateActuator.createWitness` allows any funded account to permissionlessly register a new witness by paying `AccountUpgradeCost`, with no cap on the total number of witnesses stored in `WitnessStore` [1](#0-0) . This unboundedly growing witness set is then iterated in full, multiple times per maintenance cycle, inside consensus-critical, non-gas-metered code.

### Title
Unbounded Iteration Over Permissionlessly-Growable Witness Set in `MaintenanceManager::doMaintenance` Can Cause Consensus-Critical Processing Delay/DoS - (consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java)

### Summary
Every maintenance cycle, `MaintenanceManager.doMaintenance()` calls `consensusDelegate.getAllWitnesses()` (which delegates to `WitnessStore.getAllWitnesses()`, a full store iteration) up to three separate times, and iterates the full result set each time to update per-witness VI accumulation, build the new witness address list, and set brokerage/vote snapshots for the next cycle [2](#0-1) [3](#0-2) [4](#0-3) . The `WitnessStore.getAllWitnesses()` method streams the entire underlying witness DB, returning every registered witness, not just the active/top set [5](#0-4) . Registration of a new witness via `WitnessCreateActuator` is permissionless and unbounded in count — any account with sufficient balance (`AccountUpgradeCost`) can register as a witness, and there is no maximum cap on the total number of witnesses that can exist in the store [6](#0-5) .

### Finding Description
This is structurally analogous to the reported bug class: a periodic, automatically-triggered state-update routine (`performUpkeep` in the report; `doMaintenance` here, invoked from `applyBlock` on every block that crosses a maintenance boundary) performs full iteration over a state collection whose size is controlled by unprivileged, permissionless user actions (connecting vaults in the report; creating witnesses here) [7](#0-6) . Unlike a Chainlink Keeper upkeep, `doMaintenance` is not subject to an EVM/TVM gas limit — it executes in the Java block-processing path — so the failure mode is not "revert" but unbounded CPU/wall-clock time consumed during block application, directly on the consensus-critical path. As the witness set grows (registration cost is fixed and does not scale with existing witness count), the three full-collection iterations and repeated per-witness DB reads/writes (`consensusDelegate.getWitness`, `consensusDelegate.saveWitness`) in `doMaintenance` grow linearly, increasing block-processing time for every node in the network at every maintenance boundary.

### Impact Explanation
If the witness set grows large enough (an attacker or coordinated actors could pay the upgrade cost repeatedly with different accounts), `doMaintenance` execution time increases for all full nodes/witnesses processing maintenance blocks. Because this runs synchronously inside `applyBlock`, a sufficiently bloated witness table can slow or stall block production/validation across the network at maintenance boundaries — a DoS against block processing / consensus liveness, which is more severe than the reported keeper failure since there is no external gas ceiling to bound the damage, only wall-clock/database I/O cost.

### Likelihood Explanation
Likelihood is moderate: `WitnessCreateContract` submission is a standard, unprivileged, low-cost transaction type (cost is `AccountUpgradeCost`, a fixed, not-escalating fee) [8](#0-7) , reachable by any anonymous broadcast transaction, and there is no validation-time cap on total witness count in `WitnessCreateActuator.validate()` [9](#0-8) . Actually causing network-impacting slowdown requires funding many accounts to reach the upgrade cost repeatedly, which raises the economic cost of the attack but does not require any privileged access.

### Recommendation
- Introduce and enforce a maximum total witness count (or an escalating registration cost) in `WitnessCreateActuator.validate()` to bound `WitnessStore` size.
- In `MaintenanceManager.doMaintenance()`, avoid repeated full-store `getAllWitnesses()` calls; cache the result once per maintenance cycle and reuse it instead of calling it independently up to three times.
- Consider processing witness VI accumulation and brokerage/vote snapshot updates via cursor/streaming iteration with bounded batch sizes instead of materializing and iterating the full list synchronously within block application.

### Proof of Concept
Not independently reproducible from the index alone (no local test harness available in this pass); the control-flow evidence is: `WitnessCreateActuator.createWitness` performs no cap check and persists an unbounded number of `WitnessCapsule` entries [10](#0-9) , and `MaintenanceManager.doMaintenance` unconditionally performs full-collection operations over `consensusDelegate.getAllWitnesses()` on every maintenance-boundary block [11](#0-10) . Growing the witness table via repeated `WitnessCreateContract` transactions and measuring `doMaintenance` execution time across maintenance cycles would demonstrate the linear-cost DoS vector.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java (L53-148)
```java
  @Override
  public boolean validate() throws ContractValidateException {
    if (this.any == null) {
      throw new ContractValidateException(ActuatorConstant.CONTRACT_NOT_EXIST);
    }
    if (chainBaseManager == null) {
      throw new ContractValidateException(ActuatorConstant.STORE_NOT_EXIST);
    }
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    WitnessStore witnessStore = chainBaseManager.getWitnessStore();
    if (!this.any.is(WitnessCreateContract.class)) {
      throw new ContractValidateException(
          "contract type error, expected type [WitnessCreateContract],real type[" + any
              .getClass() + "]");
    }
    final WitnessCreateContract contract;
    try {
      contract = this.any.unpack(WitnessCreateContract.class);
    } catch (InvalidProtocolBufferException e) {
      throw new ContractValidateException(e.getMessage());
    }

    byte[] ownerAddress = contract.getOwnerAddress().toByteArray();
    String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);

    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }

    if (!TransactionUtil.validUrl(contract.getUrl().toByteArray())) {
      throw new ContractValidateException("Invalid url");
    }

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);

    if (accountCapsule == null) {
      throw new ContractValidateException("account[" + readableOwnerAddress
          + ActuatorConstant.NOT_EXIST_STR);
    }
    /* todo later
    if (ArrayUtils.isEmpty(accountCapsule.getAccountName().toByteArray())) {
      throw new ContractValidateException("accountStore name not set");
    } */

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
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L57-76)
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

**File:** chainbase/src/main/java/org/tron/core/store/WitnessStore.java (L32-36)
```java
  public List<WitnessCapsule> getAllWitnesses() {
    return Streams.stream(iterator())
        .map(Entry::getValue)
        .collect(Collectors.toList());
  }
```
