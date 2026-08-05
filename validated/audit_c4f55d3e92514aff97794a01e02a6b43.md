### Title
Unbounded witness registration allows array-growth DoS of the periodic `doMaintenance()` consensus routine - (File: `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java`)

### Summary
`WitnessCreateActuator` lets any account become a witness candidate with no cap on the total number of witnesses in the system, other than paying `AccountUpgradeCost` once. `MaintenanceManager.doMaintenance()`, which runs automatically on every maintenance-time boundary (`block.maintenanceTimeInterval`, default 6 hours), repeatedly iterates the **entire** unfiltered witness set (`consensusDelegate.getAllWitnesses()`) rather than a bounded subset. This mirrors the CoreDAO finding: an attacker who can afford the per-registration fee can inflate an on-chain "candidate" array without limit, and a critical periodic routine that loops over that whole array can be pushed toward resource exhaustion / performance degradation.

### Finding Description
Registration has no upper bound on the number of witnesses: [1](#0-0) 
The only checks are that the account exists, isn't already a witness, and has enough balance to cover `AccountUpgradeCost`: [2](#0-1) 
Each new witness is persisted permanently into `WitnessStore` via `witnessStore.put(...)`: [3](#0-2) 

`WitnessStore.getAllWitnesses()` loads and returns every witness ever registered, with no cap: [4](#0-3) 

This unbounded list is consumed directly, multiple times, inside `MaintenanceManager.doMaintenance()`, which is invoked automatically by `applyBlock()` whenever the maintenance time boundary is crossed (approximately every 6 hours by default, `maintenanceTimeInterval = 21600000`): [5](#0-4) 
Inside `doMaintenance()`, `consensusDelegate.getAllWitnesses()` (backed by the unbounded `getAllWitnesses()`) is iterated at least three separate times over the full set — once to accumulate reward Vi, once to build `newWitnessAddressList`, and once to update the brokerage/vote snapshot for the next cycle: [6](#0-5) [7](#0-6) 

Unlike the active/standby selection, which is explicitly capped (`MAX_ACTIVE_WITNESS_NUM = 27`, `WITNESS_STANDBY_LENGTH = 127`): [8](#0-7) 
these caps are applied only *after* selecting/sorting from `getAllWitnesses()` (e.g. in `DposService.updateWitness` and `WitnessStore.getWitnessStandby`) — the underlying full iteration over every ever-registered witness in `doMaintenance()` and in `getAllWitnesses()`-based paths is not bounded by these constants: [9](#0-8) 

This is structurally the same bug class as the CoreDAO report: unbounded, unprivileged registration inflates an array that a critical periodic routine (`turnRound()` in CoreDAO ↔ `doMaintenance()` here) iterates in full.

### Impact Explanation
`doMaintenance()` runs deterministically as part of consensus block processing (`applyBlock` → `doMaintenance`), not as an optional/administrative operation. If the witness set is inflated to a very large size, each maintenance cycle's CPU time, memory, and DB read cost grow linearly (or worse, with the `forEach` over `DelegationStore` writes for every witness), which can:
- Cause the block whose timestamp crosses the maintenance boundary to take excessively long to process, risking missed block-production slots or timeouts across the network (all full nodes execute `doMaintenance()` identically).
- Degrade node performance/liveness at a fixed, attacker-predictable cadence.

This maps to an "invalid-state/divergence/halt"-class impact: a public, permissionless write path used to grow an unbounded array that a core consensus routine must fully traverse on a schedule.

### Likelihood Explanation
Becoming a witness costs `AccountUpgradeCost` (a real but bounded, currency-denominated cost) per registration, same economic friction model as CoreDAO's `requiredMargin`. There is no on-chain limit preventing an attacker with sufficient TRX from registering many thousands of witness accounts over time (unlike the active-participation caps, which only bound *rewarded/active* witnesses, not the raw candidate count that `getAllWitnesses()`/`doMaintenance()` traverse). Because the fee is a one-time cost and the resulting bloat is permanent and cumulative (no witness pruning path was found), likelihood is comparable to the original CoreDAO finding (moderate — gated by cost, not by any hard cap).

### Recommendation
Introduce an explicit maximum on the number of registered witnesses (analogous to CoreDAO's `CANDIDATE_COUNT_LIMIT`), enforced in `WitnessCreateActuator.validate()` (reject registration once `witnessStore` size reaches the cap), and/or refactor `MaintenanceManager.doMaintenance()` to operate only over the bounded active/standby witness set rather than `getAllWitnesses()` for the per-cycle bookkeeping loops.

### Proof of Concept
Conceptually (mirroring the Halborn PoC pattern): create N distinct funded accounts, each calling `WitnessCreateContract` via `WitnessCreateActuator` to register as a witness (each only needs to pass balance ≥ `AccountUpgradeCost` and a distinct address — no global cap check exists). After N registrations, trigger a block whose timestamp passes the maintenance boundary; `MaintenanceManager.doMaintenance()` will call `consensusDelegate.getAllWitnesses()` and iterate the full N-sized list multiple times, with cost scaling linearly (and DB-write cost from `delegationStore.setBrokerage/setWitnessVote` scaling similarly), directly demonstrating the unbounded-loop cost growth analogous to the CoreDAO `turnRound()` PoC.

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

**File:** chainbase/src/main/java/org/tron/core/store/WitnessStore.java (L29-36)
```java
  /**
   * get all witnesses.
   */
  public List<WitnessCapsule> getAllWitnesses() {
    return Streams.stream(iterator())
        .map(Entry::getValue)
        .collect(Collectors.toList());
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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L96-109)
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

**File:** common/src/main/java/org/tron/core/config/Parameter.java (L66-67)
```java
    public static final int MAX_ACTIVE_WITNESS_NUM = 27;
    public static final int WITNESS_STANDBY_LENGTH = 127;
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
