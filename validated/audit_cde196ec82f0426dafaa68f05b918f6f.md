### Title
DelegateResourceActuator allows leaving frozen balance dust below TRX_PRECISION, producing non-usable resource weight - (File: actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java)

### Summary
`DelegateResourceActuator.validate()` enforces a minimum of `TRX_PRECISION` (1 TRX) for a new delegation amount, but it never checks that the *remaining* frozen V2 balance left in the owner's account after the delegation is either zero or still at least `TRX_PRECISION`. This is the same bug class as the wearables report: an operation only bounds the amount being moved, not the residual balance left behind, so a user can be left with a sub-unit remainder that produces no resource weight.

### Finding Description
`DelegateResourceActuator.validate()` only checks that `delegateBalance >= TRX_PRECISION` and that it does not exceed the account's available `FrozenV2` balance: [1](#0-0) 

There is no check that `frozenV2BalanceForBandwidth/Energy - delegateBalance` remains `0` or `>= TRX_PRECISION`. Compare this with the minimum-unit enforcement used for new freezes in `FreezeBalanceV2Actuator.validate()`, which requires `frozenBalance >= TRX_PRECISION`: [2](#0-1) 

Resource weight (both net and energy) is computed by integer-dividing the frozen balance by `TRX_PRECISION`: [3](#0-2) 

So any frozen amount below `TRX_PRECISION` contributes zero weight (`x / TRX_PRECISION == 0` for `x < 1_000_000`). By calling `DelegateResourceContract` with a `delegateBalance` chosen so the leftover `frozenV2Balance - delegateBalance` is, e.g., `1` sun, the owner's own remaining frozen balance becomes "dead" — it is still locked/frozen but yields no bandwidth/energy weight, and it also can no longer be delegated further on its own (a fresh `DelegateResourceContract` for that dust would fail the `delegateBalance < TRX_PRECISION` check). It would need to go through `UnfreezeBalanceV2Actuator` and wait the unfreeze-delay period to become spendable again, since `UnfreezeBalanceV2Actuator.checkUnfreezeBalance` imposes no minimum: [4](#0-3) 

### Impact Explanation
Impact is limited in scope: the affected value is only the sub-1-TRX remainder, and it is not permanently lost — it can eventually be recovered via `UnfreezeBalanceV2Contract` after the unfreeze delay. However, until then it sits as frozen balance contributing zero resource weight, effectively wasting that portion of the user's frozen TRX and forcing an extra transaction/waiting period to reclaim it, mirroring the "balance going below the base unit" class of bug from the report.

### Likelihood Explanation
Medium-low: any account performing `DelegateResourceContract` (reachable directly via a broadcast transaction or via the TVM `delegateResourceContract` precompiled path) can trigger this simply by choosing a `delegateBalance` that does not leave the remaining frozen balance at `0` or `>= TRX_PRECISION`. No privileged role is required.

### Recommendation
In `DelegateResourceActuator.validate()`, after computing the available frozen V2 balance for the given resource, require that `availableBalance - delegateBalance` is either exactly `0` or `>= TRX_PRECISION`, consistent with the minimum-unit invariant already enforced when freezing (`FreezeBalanceV2Actuator`) and delegating (`delegateBalance >= TRX_PRECISION`).

### Proof of Concept
1. Freeze `2 TRX` for BANDWIDTH via `FreezeBalanceV2Contract` (satisfies `frozenBalance >= TRX_PRECISION`).
2. Submit `DelegateResourceContract` with `delegateBalance = 2_000_000 - 1` sun (i.e., `1_999_999` sun), which is `>= TRX_PRECISION` and `<=` available frozen balance, so `validate()` passes.
3. After execution, the owner's remaining `frozenV2BalanceForBandwidth` is `1` sun — below `TRX_PRECISION` — contributing `0` to `totalNetWeight` per `newNetWeight` computation in `FreezeBalanceV2Processor.execute` style logic, and it cannot be delegated again directly (a new `DelegateResourceContract` for `1` sun would fail the `< TRX_PRECISION` check in `DelegateResourceActuator.validate()`), requiring an `UnfreezeBalanceV2Contract` and the unfreeze-delay wait to become usable balance again.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L147-169)
```java
    long delegateBalance = delegateResourceContract.getBalance();
    if (delegateBalance < TRX_PRECISION) {
      throw new ContractValidateException("delegateBalance must be greater than or equal to 1 TRX");
    }

    switch (delegateResourceContract.getResource()) {
      case BANDWIDTH: {
        BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
        processor.updateUsageForDelegated(ownerCapsule);

        long accountNetUsage = ownerCapsule.getNetUsage();
        if (null != this.getTx() && this.getTx().isTransactionCreate()) {
          accountNetUsage += TransactionUtil.estimateConsumeBandWidthSize(dynamicStore,
                  ownerCapsule.getFrozenV2BalanceForBandwidth());
        }
        long netUsage = (long) (accountNetUsage * TRX_PRECISION * ((double)
            (dynamicStore.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));
        long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage,
            this.disableJavaLangMath());
        if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) {
          throw new ContractValidateException(
              "delegateBalance must be less than or equal to available FreezeBandwidthV2 balance");
        }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L131-141)
```java
    long frozenBalance = freezeBalanceV2Contract.getFrozenBalance();
    if (frozenBalance <= 0) {
      throw new ContractValidateException("frozenBalance must be positive");
    }
    if (frozenBalance < TRX_PRECISION) {
      throw new ContractValidateException("frozenBalance must be greater than or equal to 1 TRX");
    }

    if (frozenBalance > accountCapsule.getBalance()) {
      throw new ContractValidateException("frozenBalance must be less than or equal to accountBalance");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceV2Processor.java (L78-90)
```java
    switch (param.getResourceType()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(frozenBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        repo.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(frozenBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        repo.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L207-227)
```java
  public boolean checkUnfreezeBalance(AccountCapsule accountCapsule,
                                      final UnfreezeBalanceV2Contract unfreezeBalanceV2Contract,
                                      ResourceCode freezeType) {
    boolean checkOk = false;

    long frozenAmount = 0L;
    List<FreezeV2> freezeV2List = accountCapsule.getFrozenV2List();
    for (FreezeV2 freezeV2 : freezeV2List) {
      if (freezeV2.getType().equals(freezeType)) {
        frozenAmount = freezeV2.getAmount();
        break;
      }
    }

    if (unfreezeBalanceV2Contract.getUnfreezeBalance() > 0
        && unfreezeBalanceV2Contract.getUnfreezeBalance() <= frozenAmount) {
      checkOk = true;
    }

    return checkOk;
  }
```
