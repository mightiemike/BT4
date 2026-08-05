### Title
Unbounded witness registration causes O(n) growth of consensus-critical maintenance loops, enabling a DoS on block production - ([File: consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java])

### Summary
The external report describes Curve-style unbounded loops (`LiquidityGauge`/`VotingEscrow`/`GaugeController`) that iterate over user-controlled state (gauges, periods) and can grow large enough to exceed the gas limit, permanently trapping the contract. The closest reachable analog in java-tron is `WitnessCreateActuator`, which lets any funded account permissionlessly register as a witness for a flat fee, with no cap on the total number of witnesses ever registered. Every maintenance cycle, `MaintenanceManager.doMaintenance()` performs multiple full O(n) scans over **all** registered witnesses (not just the active 27/127), and these scans run synchronously inside block-production/consensus code.

### Finding Description
`WitnessCreateActuator.validate()`/`createWitness()` only checks that the caller does not already have a witness registered and has enough balance to pay `dynamicStore.getAccountUpgradeCost()` — there is no limit on the total number of `WitnessCapsule` entries that can exist in `WitnessStore`. [1](#0-0) [2](#0-1) 

Every maintenance cycle (triggered from `applyBlock` during normal block processing), `MaintenanceManager.doMaintenance()` performs several unbounded loops over `consensusDelegate.getAllWitnesses()` — a full scan of every witness ever created, not just the active set:
- Accumulating Vi rewards for every witness when the new reward algorithm is enabled: [3](#0-2) 
- Rebuilding the full witness address list to feed into sorting/selection: [4](#0-3) 
- Persisting brokerage/vote snapshots for every witness on cycle rollover: [5](#0-4) 

`WitnessStore.getAllWitnesses()` performs a full key-value store iteration and `sortWitnesses`/`sortWitness` then sort that entire (unbounded) collection, invoked from `DposService.updateWitness()`: [6](#0-5) [7](#0-6) 

`updateWitness` only truncates the *result* to `MAX_ACTIVE_WITNESS_NUM` after doing the full sort of every witness, so the cap only limits which witnesses become active/standby — it does not bound the cost of computing that result. The same unbounded `getAllWitnesses()`/sort pattern is also reachable from the public read API (`Wallet.getPaginatedNowWitnessList`), further amplifying the cost surface: [8](#0-7) 

I could not locate any dynamic-property setting, growing fee curve, or hard cap on total witness registrations in `DynamicPropertiesStore`/`Parameter.java` that would bound this growth (searches for `MAX_WITNESS`, `WITNESS_STANDBY_LENGTH`, and `AccountUpgradeCost` scaling logic did not turn up any such guard). This is analogous to the report's `GaugeController` scenario, where "Bob adds hundreds of gauges" and subsequently most functions cannot execute — here, an attacker who registers a very large number of witnesses causes every maintenance-cycle computation (which runs on every full/witness node as part of core consensus processing) to scale linearly (or worse, with sorting, O(n log n)) with the total number of ever-registered witnesses.

### Impact Explanation
Unlike the TVM-metered contracts in the original report, `MaintenanceManager.doMaintenance()` runs in the Java consensus layer with no gas metering — it can only be bounded by wall-clock/CPU time, and it is invoked synchronously as part of block application (`applyBlock` → `doMaintenance()`), which occurs on every full node/witness node every ~6 hours (each maintenance interval) or more frequently depending on configuration. If the loop over all witnesses becomes large enough (attacker-funded mass registration), the maintenance step could measurably slow down block processing across the network, delaying or disrupting block production/propagation for all participants — a state/consensus availability impact rather than an isolated smart-contract trap, but rooted in the same "unbounded loop over attacker-growable state executed unconditionally" bug class as the reported finding.

### Likelihood Explanation
This requires an attacker to pay `getAccountUpgradeCost()` TRX per witness registration, which is a real economic cost (in stock java-tron this is typically a substantial fixed amount, e.g. thousands of TRX), acting as some natural friction. This makes the attack expensive but not privileged — any account with sufficient balance can trigger it, matching the "malicious normal user abusing valid product flows" profile. Exact severity depends on the configured `AccountUpgradeCost` value and current mainnet witness count/scale, which I could not fully verify from the indexed code (the constant/config for `accountUpgradeCost` default value was not found in the excerpts available). This limits confidence in precisely quantifying the number of witnesses/cost needed to cause an observable slowdown.

### Recommendation
- Cap the total number of registered witnesses (not just the active/standby set) enforced in `WitnessCreateActuator.validate()`.
- Make the registration cost increase with the current total witness count (progressive pricing) to disincentivize mass registration.
- In `MaintenanceManager.doMaintenance()` and `WitnessStore`, avoid full-collection operations that scale with total ever-registered witnesses; only necessary computations (Vi accumulation, brokerage/vote snapshotting) should be scoped to witnesses that are actually eligible/active, or made incremental.
- Add gas/time-budgeted maintenance processing (analogous to the report's "allow iteration over periods in multiple transactions") if witness counts are expected to grow.

### Proof of Concept
Not independently executed; reasoning is based on static code tracing: an attacker with sufficient TRX repeatedly submits `WitnessCreateContract` transactions from distinct funded accounts (`WitnessCreateActuator.createWitness`), each succeeding since there is no cap on `WitnessStore` size, only a per-account existence check. Once thousands of witnesses exist, every subsequent maintenance cycle invokes `MaintenanceManager.doMaintenance()`, which calls `consensusDelegate.getAllWitnesses()` (full DB scan) multiple times and `dposService.updateWitness()` (full sort of all witnesses), scaling processing cost with the attacker-controlled witness count, mirroring the reported `GaugeController` DoS pattern where "Bob adds hundreds of gauges" and downstream functions become impractical to execute.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java (L98-106)
```java
    if (witnessStore.has(ownerAddress)) {
      throw new ContractValidateException(
          WITNESS_EXCEPTION_STR + readableOwnerAddress + "] has existed");
    }

    if (accountCapsule.getBalance() < dynamicStore
        .getAccountUpgradeCost()) {
      throw new ContractValidateException("balance < AccountUpgradeCost");
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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L96-101)
```java
    if (dynamicPropertiesStore.useNewRewardAlgorithm()) {
      long curCycle = dynamicPropertiesStore.getCurrentCycleNumber();
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L107-109)
```java
      List<ByteString> newWitnessAddressList = new ArrayList<>();
      consensusDelegate.getAllWitnesses()
          .forEach(witnessCapsule -> newWitnessAddressList.add(witnessCapsule.getAddress()));
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

**File:** chainbase/src/main/java/org/tron/core/store/WitnessStore.java (L32-63)
```java
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

  public static void sortWitnesses(List<WitnessCapsule> witnesses, boolean isSortOpt) {
    witnesses.sort(Comparator.comparingLong(WitnessCapsule::getVoteCount).reversed()
        .thenComparing(isSortOpt
            ? Comparator.comparing(WitnessCapsule::createReadableString).reversed()
            : Comparator.comparingInt((WitnessCapsule w) -> w.getAddress().hashCode()).reversed()));
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

**File:** framework/src/main/java/org/tron/core/Wallet.java (L793-810)
```java
    List<WitnessCapsule> witnessCapsuleList = chainBaseManager.getWitnessStore().getAllWitnesses();
    if (offset >= witnessCapsuleList.size()) {
      return null;
    }

    VotesStore votesStore = chainBaseManager.getVotesStore();
    // Count the vote changes for each witness in the current epoch, it is maybe negative.
    Map<ByteString, Long> countWitness = countVote(votesStore);

    // Iterate through the witness list to apply vote changes and calculate the real-time vote count
    witnessCapsuleList.forEach(witnessCapsule -> {
      long voteCount = countWitness.getOrDefault(witnessCapsule.getAddress(), 0L);
      witnessCapsule.setVoteCount(witnessCapsule.getVoteCount() + voteCount);
    });

    // Use the same sorting logic as in the Maintenance period
    WitnessStore.sortWitnesses(witnessCapsuleList,
        chainBaseManager.getDynamicPropertiesStore().allowWitnessSortOptimization());
```
