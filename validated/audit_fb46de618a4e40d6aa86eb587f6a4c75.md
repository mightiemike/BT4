### Title
Unbounded per-witness loop in `MaintenanceManager.doMaintenance()` allows griefing-driven DoS of consensus maintenance - (File: `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java`)

### Summary
`WitnessCreateActuator` lets any account with sufficient balance (`AccountUpgradeCost`, a fixed, one-time, non-scaling fee) register as a witness candidate with no cap on the total number of candidates. Every maintenance cycle, `MaintenanceManager.doMaintenance()` iterates several times over the *entire* set of registered witnesses (`consensusDelegate.getAllWitnesses()`), not just the small active/standby sets, performing per-witness store reads/writes. This mirrors the NFTX `distribute()` pattern: an unbounded, attacker-growable list is iterated inside privileged/consensus-critical code triggered on a fixed schedule (analogous to being triggered "within the context of user transactions" in the NFTX report), so the cost of the operation grows linearly with attacker-created state.

### Finding Description
`WitnessCreateActuator.validate()`/`createWitness()` only checks: address validity, URL validity, account existence, that the account is not already a witness, and that the balance covers `dynamicStore.getAccountUpgradeCost()`. [1](#0-0) 
There is no limit on the total number of witnesses that can be created — any number of distinct funded accounts can each pay the fixed cost and register. [2](#0-1) 

`MaintenanceManager.doMaintenance()` is invoked automatically by `applyBlock()` whenever a block crosses the maintenance-time boundary (i.e., unconditionally, on a schedule, not gated by any specific user transaction, and not skippable): [3](#0-2) 

Inside `doMaintenance()`, the code performs **multiple full iterations over `consensusDelegate.getAllWitnesses()`** — the complete list of registered witness candidates, not the bounded active-witness (27) or standby (127) subsets:
- Vi accumulation loop (when `useNewRewardAlgorithm()` is enabled): [4](#0-3) 
- Building the full candidate address list for `dposService.updateWitness()` / `incentiveManager.reward()`: [5](#0-4) 
- Per-cycle brokerage/vote snapshot loop: [6](#0-5) 

Each iteration performs store reads and writes (`delegationStore.accumulateWitnessVi`, `delegationStore.setBrokerage`, `delegationStore.setWitnessVote`), so the total maintenance work scales linearly (and with multiple full-DB-touching passes, effectively as a multiple) with the number of ever-created witness candidates — a value fully controlled by unprivileged attackers who can create arbitrarily many funded accounts and call `WitnessCreateContract` from each.

This is structurally the same class of bug as the reported NFTX issue: a periodic/triggered core routine loops over a list whose length is attacker-controlled and unbounded, so gas/CPU/time cost grows without bound, eventually threatening to make the routine unable to complete within its expected window.

### Impact Explanation
`doMaintenance()` runs synchronously as part of block application at every maintenance boundary (every 6 hours by default in java-tron) and determines the active witness set, vote tallies, and reward accounting for the entire network. If maintenance processing time grows large enough (due to a very large number of registered witness candidates) to approach or exceed the block-production window, it can delay or degrade block production system-wide, affecting all users' ability to have their transactions (mint/transfer/vote/etc.) processed in a timely manner — directly analogous to the "users unable to mint, redeem, or swap" impact called out in the NFTX report. Because this code path is part of core consensus/state-transition logic (invalid-state/divergence/halt category) rather than a peripheral feature, the potential blast radius is network-wide rather than limited to a single account.

### Likelihood Explanation
The precondition is that an attacker (or many colluding parties) fund a large number of accounts and pay `AccountUpgradeCost` (a fixed TRX amount) once per account to become a witness candidate — no witness-count cap and no increasing marginal cost exist to discourage this. This is a purely economic/capital cost, not a privileged action, and is reachable by any unprivileged user with sufficient TRX. However, exploiting it to the point of causing a measurable network-wide delay requires accumulating a very large absolute number of witness registrations (likely economically significant given the flat fee), and the loop bodies themselves are simple map/store operations rather than complex external calls (unlike NFTX's `_sendForReceiver` external-call amplification), so the per-iteration cost is much lower. This lowers immediate severity relative to the NFTX original but the structural vulnerability (unbounded loop over an attacker-growable list in a mandatory, non-skippable maintenance routine) is real and unmitigated by any cap in the code reviewed.

### Recommendation
- Introduce a hard or economically-scaling cap on the number of witness candidates that can be registered (e.g., increasing `AccountUpgradeCost` per additional candidate, or an absolute maximum candidate count enforced in `WitnessCreateActuator.validate()`).
- Restructure `doMaintenance()` to avoid multiple full passes over `getAllWitnesses()`; where possible, limit iteration to bounded sets (active + standby) and defer/batch per-cycle bookkeeping for non-active candidates, or paginate/rate-limit the per-cycle Vi-accumulation and brokerage/vote snapshot updates so a single maintenance cycle cannot be blocked by unbounded candidate growth.
- Add monitoring/alerting on `doMaintenance()` execution time and total witness-candidate count so operators can detect this growth before it threatens block timing.

### Proof of Concept
1. Fund N distinct accounts, each with at least `getAccountUpgradeCost()` TRX (a flat cost with no scaling for total candidate count).
2. From each account, submit a `WitnessCreateContract` transaction; `WitnessCreateActuator.validate()` only checks balance/address/URL and that the account is not already a witness — it never checks or limits `witnessStore` size, so all N registrations succeed. [7](#0-6) 
3. Wait for the next maintenance boundary; `applyBlock()` unconditionally invokes `doMaintenance()`. [8](#0-7) 
4. `doMaintenance()` executes multiple full-list iterations over all N candidates (Vi accumulation, witness-list rebuild, brokerage/vote snapshot), each doing store reads/writes, causing processing time to scale with N with no upper bound enforced anywhere in the reviewed code. [9](#0-8)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java (L87-106)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java (L121-148)
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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L96-162)
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
