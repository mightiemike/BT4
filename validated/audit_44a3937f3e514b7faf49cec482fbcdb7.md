## Title
Unbounded iteration over `WitnessStore` in `MaintenanceManager.doMaintenance` — DoS via witness-count growth - (File: `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java`)

### Summary
`WitnessCreateActuator` lets any funded account register as a witness with no cap on the total number of witnesses, only a per-registration cost check. [1](#0-0)  Every maintenance cycle, `MaintenanceManager.doMaintenance` performs multiple full iterations over `consensusDelegate.getAllWitnesses()`, which is backed by `WitnessStore.getAllWitnesses()` — a full scan of the witness DB with no size bound. [2](#0-1)  This is the same bug class as the reported `NFTXEligiblityManager.distribute` issue: an attacker-growable list is iterated in full during a critical, mandatory codepath, and the per-cycle cost scales linearly (here, multiplied across several passes) with the number of entries the attacker can create.

### Finding Description
`WitnessCreateActuator.validate()`/`createWitness()` only check that the caller's account balance covers `getAccountUpgradeCost()` and that the caller isn't already a witness; there is no global limit on the number of witnesses that can be registered. [3](#0-2)  An attacker controlling many funded accounts (or one account per registration) can register an arbitrarily large number of witnesses over time.

`MaintenanceManager.doMaintenance()`, which runs automatically at every maintenance cycle as part of normal block processing on every full node, calls `consensusDelegate.getAllWitnesses()` and iterates over the full result set multiple times:
- once to accumulate reward Vi values, [4](#0-3) 
- once to build `newWitnessAddressList`, [5](#0-4) 
- and again to persist brokerage/vote data for the next cycle. [6](#0-5) 

Each iteration performs a DB read/write per witness (`saveWitness`, `setBrokerage`, `setWitnessVote`), so the cost of `doMaintenance()` grows linearly (with several passes, effectively a multiple of N) with the total number of registered witnesses N, not just the ~127 active/standby witnesses. `WitnessStore.getAllWitnesses()` itself streams the entire underlying store with no cap. [2](#0-1) 

### Impact Explanation
Since `doMaintenance()` executes unconditionally on every node at every maintenance cycle (not gated by any privileged role), an attacker who inflates the witness set can increase the per-cycle CPU/DB cost across the entire network. If growth is large enough, this slows block/maintenance processing on all nodes simultaneously, which is a protocol-level availability risk (consensus-cycle DoS) rather than an isolated RPC call failure — analogous to the `distribute()` gas-limit DoS in the source report but manifesting as increased maintenance-cycle latency/resource consumption for every node instead of a single reverting transaction.

### Likelihood Explanation
Witness registration is unprivileged and only gated by a fixed TRX cost (`getAccountUpgradeCost()`), with no upper bound on the total number of witnesses that can exist. [7](#0-6)  An attacker with sufficient capital (or by reusing the fee repeatedly through many accounts) can register a large number of witnesses over time, making this reachable purely through normal broadcast transactions.

### Recommendation
Introduce a hard cap on the total number of registered witnesses in `WitnessCreateActuator.validate()` (reject once `WitnessStore` size reaches a configurable maximum), and/or refactor `MaintenanceManager.doMaintenance()` to avoid repeated full scans of `getAllWitnesses()` per cycle (e.g., combine the three passes into one, or restrict expensive per-cycle bookkeeping to active/standby witnesses only rather than the entire historical witness set).

### Proof of Concept
1. Fund N distinct accounts, each above `getAccountUpgradeCost()`.
2. Broadcast N `WitnessCreateContract` transactions, one per account — all succeed since there is no cap. [8](#0-7) 
3. At the next maintenance cycle, `MaintenanceManager.doMaintenance()` performs three full passes over the now N-sized witness list, each doing per-witness DB reads/writes. [9](#0-8) 
4. As N grows large, measure the increase in maintenance-cycle processing time on a node to demonstrate the linear (multi-pass) cost growth with attacker-controlled witness count.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java (L53-109)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java (L121-149)
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
    adjustBalance(accountStore, witnessCreateContract.getOwnerAddress().toByteArray(), -cost);
    if (dynamicStore.supportBlackHoleOptimization()) {
      dynamicStore.burnTrx(cost);
    } else {
      adjustBalance(accountStore, accountStore.getBlackhole(), +cost);
    }
    dynamicStore.addTotalCreateWitnessCost(cost);
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
