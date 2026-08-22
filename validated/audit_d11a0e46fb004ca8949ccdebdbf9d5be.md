### Title
Unbounded, low-cost witness registration enables permissionless DoS amplification of `MaintenanceManager.doMaintenance()` - (File: consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java)

### Summary
The Code4rena finding describes a class of bug where a privileged actor can cheaply create an unbounded data structure (Juicebox splits) that a later, unprivileged, permissionless operation must fully iterate over, causing the caller of that later operation to hit a resource limit and lose value with no corresponding cost to the party who created the oversized structure. The closest reachable analog in java-tron is witness registration: `WitnessCreateActuator` lets any account permissionlessly register as a witness for a fixed fee with no cap on the total number of witnesses that can exist, and every witness so created is later iterated multiple times per maintenance cycle by `MaintenanceManager.doMaintenance()`, which every full node/validator must execute deterministically as part of consensus.

### Finding Description
`WitnessCreateActuator.validate()` only checks that the account exists, has sufficient balance for `getAccountUpgradeCost()`, and that the address isn't already a witness — there is no check on the total number of existing witnesses in the system. [1](#0-0) 

`WitnessCreateActuator.createWitness()` then unconditionally inserts a new `WitnessCapsule` into `WitnessStore`. [2](#0-1) 

Every maintenance cycle (roughly every 6 hours, driven by `MaintenanceManager.applyBlock()`), `doMaintenance()` performs several full scans over `consensusDelegate.getAllWitnesses()` — once to accumulate vote-interest (`accumulateWitnessVi`), once to build `newWitnessAddressList`, and once more (conditionally) to set brokerage/vote snapshots for the next cycle: [3](#0-2) 

This work is executed by **every full node in the network** as part of deterministic block/consensus processing, not gated by any per-transaction energy/bandwidth accounting the way TVM contract calls are. Unlike the vote arrays processed by `VoteWitnessProcessor`, which are explicitly capped by `MAX_VOTE_NUMBER`, [4](#0-3) 
there is no analogous cap on the *global* witness set that `doMaintenance()` iterates. Only the count of *active* (top-N) witnesses is capped via `MAX_ACTIVE_WITNESS_NUM` in `DposService.updateWitness()`, [5](#0-4) 
but that cap only limits the *active* set used for block production, not the total registered-witness population that `getAllWitnesses()` returns and that `doMaintenance()` fully scans.

The economic barrier is a single fixed fee (`getAccountUpgradeCost()`, a `DynamicPropertiesStore` governance parameter, historically 9999 TRX). This mirrors the root cause of the Juicebox bug precisely: an unprivileged actor pays a modest, fixed, per-item cost to grow an unbounded protocol-level list, while a separate, mandatory, deterministic operation over that same list becomes increasingly expensive for others (in Juicebox: the caller of `distributeReservedTokensOf`; in java-tron: every validator node executing consensus).

### Impact Explanation
If enough accounts are registered as witnesses, `doMaintenance()`'s repeated full scans (`getAllWitnesses()` called 2–3 times per cycle, each doing per-witness store reads/writes) grow linearly with witness count and are executed synchronously by every node during block processing at the maintenance boundary block. A sufficiently large witness population can materially slow down or stall block production for the entire network at that block boundary — a consensus-level availability degradation, not merely a single-actor griefing loss. This differs from the Code4rena report's "honeypot griefs one caller for gas" framing, but is a stronger-impact structural analog: no user-set minimum/size cap on an attacker-influenceable list that a critical protocol routine must fully traverse.

### Likelihood Explanation
Likelihood is bounded primarily by the economic cost of `getAccountUpgradeCost()` per registration and by the fact this is a governance-configurable value rather than a hardcoded, disproportionately-small constant like the Juicebox `percent` field. An attacker with sufficient capital (or during a period when the parameter is lowered via a passed proposal) could register a large number of witness accounts to inflate the scanned set; each individual registration is cheap relative to the aggregate protocol cost imposed on every node every maintenance cycle, matching the original bug's core asymmetry (cheap to create the item, expensive-per-scale to process it, but the entity setting it up doesn't bear that scaling cost).

### Recommendation
Enforce an explicit upper bound on the number of registered (non-active) witnesses in `WitnessCreateActuator.validate()` (e.g., reject creation once `witnessStore.getAllWitnesses().size()` exceeds a sensible cap), analogous to the `MAX_VOTE_NUMBER` bound already applied to per-account vote arrays. Alternatively/additionally, make `doMaintenance()`'s per-cycle work bounded independent of the total registered-witness count (e.g., only process witnesses with nonzero votes/activity), rather than relying solely on the fixed TRX fee as an economic deterrent.

### Proof of Concept
Not independently reproducible from static analysis alone — this requires running a devnet, driving repeated `WitnessCreateContract` broadcasts from many funded accounts, and measuring `doMaintenance()` wall-clock time at the maintenance boundary block as witness count scales, to empirically confirm the growth trend implied by the unbounded `getAllWitnesses().forEach(...)` loops shown above.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java (L53-108)
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L28-37)
```java
  public void validate(VoteWitnessParam param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    if (param.getVotes().size() > MAX_VOTE_NUMBER) {
      throw new ContractValidateException(
          "VoteNumber more than maxVoteNumber " + MAX_VOTE_NUMBER);
    }
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
