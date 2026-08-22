[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L43-48)
```java
  @Getter
  private static final int UNFREEZE_MAX_TIMES = 32;

  public UnfreezeBalanceV2Actuator() {
    super(ContractType.UnfreezeBalanceV2Contract, UnfreezeBalanceV2Contract.class);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/CancelAllUnfreezeV2Actuator.java (L57-82)
```java
    AccountCapsule ownerCapsule = accountStore.get(ownerAddress);
    List<UnFreezeV2> unfrozenV2List = ownerCapsule.getUnfrozenV2List();
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    AtomicLong atomicWithdrawExpireBalance = new AtomicLong(0L);
    /* The triple object is defined by resource type, with left representing the pair object
    corresponding to bandwidth, middle representing the pair object corresponding to energy, and
    right representing the pair object corresponding to tron power. The pair object for each
    resource type, left represents resource weight, and right represents the number of unfreeze
    resources for that resource type. */
    Triple<Pair<AtomicLong, AtomicLong>, Pair<AtomicLong, AtomicLong>, Pair<AtomicLong, AtomicLong>>
        triple = Triple.of(
        Pair.of(new AtomicLong(0L), new AtomicLong(0L)),
        Pair.of(new AtomicLong(0L), new AtomicLong(0L)),
        Pair.of(new AtomicLong(0L), new AtomicLong(0L)));
    for (UnFreezeV2 unFreezeV2 : unfrozenV2List) {
      updateAndCalculate(triple, ownerCapsule, now, atomicWithdrawExpireBalance, unFreezeV2);
    }
    ownerCapsule.clearUnfrozenV2();
    addTotalResourceWeight(dynamicStore, triple);

    long withdrawExpireBalance = atomicWithdrawExpireBalance.get();
    if (withdrawExpireBalance > 0) {
      ownerCapsule.setBalance(ownerCapsule.getBalance() + withdrawExpireBalance);
    }

    accountStore.put(ownerCapsule.createDbKey(), ownerCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/CancelAllUnfreezeV2Actuator.java (L176-205)
```java
  public void updateFrozenInfoAndTotalResourceWeight(
      AccountCapsule accountCapsule, UnFreezeV2 unFreezeV2,
      Triple<Pair<AtomicLong, AtomicLong>, Pair<AtomicLong, AtomicLong>,
          Pair<AtomicLong, AtomicLong>> triple) {
    switch (unFreezeV2.getType()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(unFreezeV2.getUnfreezeAmount());
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        triple.getLeft().getLeft().addAndGet(newNetWeight - oldNetWeight);
        triple.getLeft().getRight().addAndGet(unFreezeV2.getUnfreezeAmount());
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(unFreezeV2.getUnfreezeAmount());
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        triple.getMiddle().getLeft().addAndGet(newEnergyWeight - oldEnergyWeight);
        triple.getMiddle().getRight().addAndGet(unFreezeV2.getUnfreezeAmount());
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(unFreezeV2.getUnfreezeAmount());
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        triple.getRight().getLeft().addAndGet(newTPWeight - oldTPWeight);
        triple.getRight().getRight().addAndGet(unFreezeV2.getUnfreezeAmount());
        break;
      default:
        break;
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java (L143-146)
```java
  @Override
  public long calcFee() {
    return 0;
  }
```
