### Title
Third-party resource accounting can be corrupted via same-transaction manipulation of `TotalNetWeight`/`TotalEnergyWeight` before `unDelegateResource` — analog of the Folks Finance "flashloan → rebalanceUp" pattern - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java])

### Summary
The Folks Finance bug lets an attacker atomically manipulate a shared/global rate metric (pool utilization/variable rate) via a flashloan, then invoke a permissionless function (`rebalanceUp`) that permanently mutates a *third party's* persistent loan state based on that manipulated metric, then reverse the manipulation — leaving the victim stuck with corrupted state. The closest reachable analog in java-tron is the TVM-exposed `unDelegateResource` native contract, whose calculation of how much bandwidth/energy usage to strip from the **receiver** (a third-party account, not the caller) depends on the network-wide aggregates `TotalNetWeight` / `TotalEnergyWeight`, which a caller can transiently change in the same atomic transaction via `freezeBalanceV2`/`unfreezeBalanceV2` (also exposed as TVM opcodes/native contracts), then revert.

### Finding Description
`DelegateResourceContract`/`UnDelegateResourceContract` allow an owner to delegate/undelegate BANDWIDTH/ENERGY to a distinct `receiverAddress` [1](#0-0) . These are also reachable from smart-contract execution as native/TVM operations `DELEGATERESOURCE` / `UNDELEGATERESOURCE` and `FREEZEBALANCEV2` / `UNFREEZEBALANCEV2`, all executed synchronously within the same call frame via `Program.delegateResource`, `Program.freezeBalanceV2`, etc. [2](#0-1) [3](#0-2) [4](#0-3) .

In `UnDelegateResourceProcessor.execute()`, when the owner calls `unDelegateResource` against a *receiver*, the amount of bandwidth/energy usage stripped from the receiver's persistent `netUsage`/`energyUsage` is computed proportionally to `repo.getTotalNetWeight()` / `dynamicStore.getTotalNetLimit()` (or the energy equivalent) — a chain-wide aggregate, not something tied to the receiver's own state: [5](#0-4) 

The same pattern also appears in `DelegateResourceProcessor.validate()`, which reads `repo.getTotalNetWeight()`/`repo.getTotalEnergyWeight()` to bound how much can be delegated [6](#0-5) .

`TotalNetWeight`/`TotalEnergyWeight` are live, mutable, chain-wide counters that change immediately (within the same transaction) whenever `freezeBalanceV2`/`unfreezeBalanceV2` is invoked, as confirmed by the existing test assertions that check `dynamicStore.getTotalNetWeight()`/`getTotalEnergyWeight()` change right after a `freezeBalanceV2` call in the same transaction: [7](#0-6) .

Because `freezeBalanceV2`, `unfreezeBalanceV2`, `delegateResource`, and `unDelegateResource` are all callable as sequential VM operations inside one smart-contract transaction, an owner can, in a single atomic transaction:
1. Freeze or unfreeze a large amount of self-owned TRX to transiently inflate/deflate `TotalNetWeight`/`TotalEnergyWeight`.
2. Call `unDelegateResource` against a receiver they previously delegated resources to, causing the receiver's persistent `netUsage`/`energyUsage` to be adjusted using the manipulated aggregate (the `min(unDelegateMaxUsage, transferUsage)` cap in `UnDelegateResourceProcessor.execute` becomes miscalibrated).
3. Reverse the freeze/unfreeze to restore the aggregate to its prior value — all before the transaction/block ends.

This mirrors the reported bug class: the persistent, third-party account state (here, the receiver's resource-usage accounting) is permanently mutated using a transiently-manipulated shared rate, and the manipulation is reverted at the end, leaving the victim's accounting corrupted.

### Impact Explanation
A miscalibrated `transferUsage` cap means the receiver's `netUsage`/`energyUsage` bookkeeping no longer reflects reality after resources are undelegated from them: the receiver can be left with an artificially inflated recorded usage (reducing their effective available free bandwidth/energy going forward) or, conversely, an artificially deflated usage (letting them appear to have more headroom than warranted, which could distort downstream `AdaptiveResourceLimit` accounting). Either direction corrupts resource/reward accounting for a third party who did not initiate or consent to the transaction, which falls into the "resource and reward accounting corruption" impact category.

### Likelihood Explanation
Medium. It requires an attacker who has previously delegated resources to a receiver (so they can call `unDelegateResource` against that address) and who owns enough TRX to meaningfully swing `TotalNetWeight`/`TotalEnergyWeight` within one transaction. No external capital lending (flashloan) is even needed since the attacker uses their own TRX transiently — freeze and unfreeze are both instantaneous state mutations to the aggregate, only the *withdrawal* of unfrozen TRX is time-locked, not the aggregate-weight change itself.

### Recommendation
- Do not use the instantaneous, same-transaction value of `TotalNetWeight`/`TotalEnergyWeight` to compute usage transfers affecting a third-party receiver's persistent accounting. Snapshot/read these aggregates at a point insulated from same-transaction manipulation (e.g., use the value as of the start of the block, or disallow `freezeBalanceV2`/`unfreezeBalanceV2` and `delegateResource`/`unDelegateResource` from being combined in a single atomic call context).
- Alternatively, restrict `unDelegateResource`/`delegateResource` from being invoked in the same transaction where `freezeBalanceV2`/`unfreezeBalanceV2` was also invoked, analogous to the report's recommended mitigation of restricting rebalance after deposit/borrow/repay in the same transaction.

### Proof of Concept
Not independently verified end-to-end in this analysis (no execution environment available). The reachability chain and the vulnerable computation are established by:
- `UnDelegateResourceProcessor.execute()` using `repo.getTotalEnergyWeight()`/`getTotalNetWeight()` to compute `transferUsage` applied to a third-party receiver [5](#0-4) 
- `freezeBalanceV2`/`unfreezeBalanceV2` immediately mutating those same aggregates within a transaction [7](#0-6) 
- All four operations (`FREEZEBALANCEV2`, `UNFREEZEBALANCEV2`, `DELEGATERESOURCE`, `UNDELEGATERESOURCE`) being callable sequentially from within one smart-contract execution [2](#0-1) 

A concrete step-by-step exploit transaction (deploy a contract that calls `freezeBalanceV2` → `unDelegateResource(victimReceiver, ...)` → `unfreezeBalanceV2` in one function, and assert the receiver's `netUsage`/`energyUsage` deviates from the value computed with the pre-manipulation aggregate) was not built/run here; this would need to be constructed and executed in a test harness such as `FreezeV2Test`/`UnDelegateResourceActuatorTest` to confirm actual exploitability and quantify the deviation.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L238-245)
```java
    byte[] receiverAddress = unDelegateResourceContract.getReceiverAddress().toByteArray();
    if (!DecodeUtil.addressValid(receiverAddress)) {
      throw new ContractValidateException("Invalid receiverAddress");
    }
    if (Arrays.equals(receiverAddress, ownerAddress)) {
      throw new ContractValidateException(
          "receiverAddress must not be the same as ownerAddress");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/OperationRegistry.java (L613-655)
```java
  public static void appendFreezeV2Operations(JumpTable table) {
    BooleanSupplier proposal = VMConfig::allowTvmFreezeV2;

    table.set(new Operation(
        Op.FREEZEBALANCEV2, 2, 1,
        EnergyCost::getFreezeBalanceV2Cost,
        OperationActions::freezeBalanceV2Action,
        proposal));

    table.set(new Operation(
        Op.UNFREEZEBALANCEV2, 2, 1,
        EnergyCost::getUnfreezeBalanceV2Cost,
        OperationActions::unfreezeBalanceV2Action,
        proposal));

    table.set(new Operation(
        Op.WITHDRAWEXPIREUNFREEZE, 0, 1,
        EnergyCost::getWithdrawExpireUnfreezeCost,
        OperationActions::withdrawExpireUnfreezeAction,
        proposal));

    table.set(new Operation(
        Op.CANCELALLUNFREEZEV2, 0, 1,
        EnergyCost::getCancelAllUnfreezeV2Cost,
        OperationActions::cancelAllUnfreezeV2Action,
        proposal));
  }

  public static void appendDelegateOperations(JumpTable table) {
    BooleanSupplier proposal = VMConfig::allowTvmFreezeV2;

    table.set(new Operation(
        Op.DELEGATERESOURCE, 3, 1,
        EnergyCost::getDelegateResourceCost,
        OperationActions::delegateResourceAction,
        proposal));

    table.set(new Operation(
        Op.UNDELEGATERESOURCE, 3, 1,
        EnergyCost::getUnDelegateResourceCost,
        OperationActions::unDelegateResourceAction,
        proposal));
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2017-2046)
```java
  public boolean freezeBalanceV2(DataWord frozenBalance, DataWord resourceType) {
    Repository repository = getContractState().newRepositoryChild();
    byte[] owner = getContextAddress();

    increaseNonce();
    InternalTransaction internalTx = addInternalTx(null, owner, owner,
        frozenBalance.longValue(), null,
        "freezeBalanceV2For" + convertResourceToString(resourceType), nonce, null);

    try {
      FreezeBalanceV2Param param = new FreezeBalanceV2Param();
      param.setOwnerAddress(owner);
      param.setResourceType(parseResourceCodeV2(resourceType));
      param.setFrozenBalance(frozenBalance.sValue().longValueExact());

      FreezeBalanceV2Processor processor = new FreezeBalanceV2Processor();
      processor.validate(param, repository);
      processor.execute(param, repository);
      repository.commit();
      return true;
    } catch (ContractValidateException e) {
      logger.warn("TVM FreezeBalanceV2: validate failure. Reason: {}", e.getMessage());
    } catch (ArithmeticException e) {
      logger.warn("TVM FreezeBalanceV2: frozenBalance out of long range.");
    }
    if (internalTx != null) {
      internalTx.reject();
    }
    return false;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2157-2189)
```java
  public boolean delegateResource(
      DataWord receiverAddress, DataWord delegateBalance, DataWord resourceType) {
    Repository repository = getContractState().newRepositoryChild();
    byte[] owner = getContextAddress();
    byte[] receiver = receiverAddress.toTronAddress();

    increaseNonce();
    InternalTransaction internalTx = addInternalTx(null, owner, receiver,
        delegateBalance.longValue(), null,
        "delegateResourceOf" + convertResourceToString(resourceType), nonce, null);

    try {
      DelegateResourceParam param = new DelegateResourceParam();
      param.setOwnerAddress(owner);
      param.setReceiverAddress(receiver);
      param.setDelegateBalance(delegateBalance.sValue().longValueExact());
      param.setResourceType(parseResourceCodeV2(resourceType));

      DelegateResourceProcessor processor = new DelegateResourceProcessor();
      processor.validate(param, repository);
      processor.execute(param, repository);
      repository.commit();
      return true;
    } catch (ContractValidateException e) {
      logger.warn("TVM DelegateResource: validate failure. Reason: {}", e.getMessage());
    } catch (ArithmeticException e) {
      logger.warn("TVM DelegateResource: balance out of long range.");
    }
    if (internalTx != null) {
      internalTx.reject();
    }
    return false;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L91-151)
```java
  public void execute(UnDelegateResourceParam param, Repository repo) {
    byte[] ownerAddress = param.getOwnerAddress();
    byte[] receiverAddress = param.getReceiverAddress();
    long unDelegateBalance = param.getUnDelegateBalance();
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddress);
    AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();
    long now = repo.getHeadSlot();

    long transferUsage = 0;
    // modify receiver Account
    if (receiverCapsule != null) {
      switch (param.getResourceType()) {
        case BANDWIDTH:
          BandwidthProcessor bandwidthProcessor = new BandwidthProcessor(ChainBaseManager.getInstance());
          bandwidthProcessor.updateUsageForDelegated(receiverCapsule);
          /* For example, in a scenario where a regular account can be upgraded to a contract
          account through an interface, the account information will be cleared after the
          contract suicide, and this account will be converted to a regular account in the future */
          if (receiverCapsule.getAcquiredDelegatedFrozenV2BalanceForBandwidth()
              < unDelegateBalance) {
            // A TVM contract suicide, re-create will produce this situation
            receiverCapsule.setAcquiredDelegatedFrozenV2BalanceForBandwidth(0);
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * dynamicStore.getTotalNetLimit() / repo.getTotalNetWeight());
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage, VMConfig.disableJavaLangMath());

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }

          long newNetUsage = receiverCapsule.getNetUsage() - transferUsage;
          receiverCapsule.setNetUsage(newNetUsage);
          receiverCapsule.setLatestConsumeTime(now);
          break;
        case ENERGY:
          EnergyProcessor energyProcessor =
              new EnergyProcessor(dynamicStore, ChainBaseManager.getInstance().getAccountStore());
          energyProcessor.updateUsage(receiverCapsule);

          if (receiverCapsule.getAcquiredDelegatedFrozenV2BalanceForEnergy()
              < unDelegateBalance) {
            // A TVM contract receiver, re-create will produce this situation
            receiverCapsule.setAcquiredDelegatedFrozenV2BalanceForEnergy(0);
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * dynamicStore.getTotalEnergyCurrentLimit() / repo.getTotalEnergyWeight());
            transferUsage = (long) (receiverCapsule.getEnergyUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForEnergy()));
            transferUsage = min(unDelegateMaxUsage, transferUsage, VMConfig.disableJavaLangMath());

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(-unDelegateBalance);
          }

          long newEnergyUsage = receiverCapsule.getEnergyUsage() - transferUsage;
          receiverCapsule.setEnergyUsage(newEnergyUsage);
          receiverCapsule.setLatestConsumeTimeForEnergy(now);
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L57-93)
```java
    boolean disableJavaLangMath = VMConfig.disableJavaLangMath();
    switch (param.getResourceType()) {
      case BANDWIDTH: {
        BandwidthProcessor processor = new BandwidthProcessor(ChainBaseManager.getInstance());
        processor.updateUsageForDelegated(ownerCapsule);

        long netUsage = (long) (ownerCapsule.getNetUsage() * TRX_PRECISION * ((double)
            (repo.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));

        long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage, disableJavaLangMath);

        if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) {
          throw new ContractValidateException(
                  "delegateBalance must be less than or equal to available FreezeBandwidthV2 balance");
        }
      }
      break;
      case ENERGY: {
        EnergyProcessor processor =
            new EnergyProcessor(dynamicStore, ChainBaseManager.getInstance().getAccountStore());
        processor.updateUsage(ownerCapsule);

        long energyUsage = (long) (ownerCapsule.getEnergyUsage() * TRX_PRECISION * ((double)
            (repo.getTotalEnergyWeight()) / dynamicStore.getTotalEnergyCurrentLimit()));

        long v2EnergyUsage = getV2EnergyUsage(ownerCapsule, energyUsage, disableJavaLangMath);

        if (ownerCapsule.getFrozenV2BalanceForEnergy() - v2EnergyUsage < delegateBalance) {
          throw new ContractValidateException(
                  "delegateBalance must be less than or equal to available FreezeEnergyV2 balance");
        }
      }
      break;
      default:
        throw new ContractValidateException(
            "Unknown ResourceCode, valid ResourceCode[BANDWIDTH、ENERGY]");
    }
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/FreezeV2Test.java (L582-627)
```java
  private TVMTestResult freezeV2(
      byte[] callerAddr, byte[] contractAddr, long frozenBalance, long res) throws Exception {
    DynamicPropertiesStore dynamicStore = dbManager.getDynamicPropertiesStore();
    long oldTotalNetWeight = dynamicStore.getTotalNetWeight();
    long oldTotalEnergyWeight = dynamicStore.getTotalEnergyWeight();
    long oldTronPowerWeight = dynamicStore.getTotalTronPowerWeight();

    AccountStore accountStore = dbManager.getAccountStore();
    AccountCapsule oldOwner = accountStore.get(contractAddr);

    TVMTestResult result =
        triggerFreeze(callerAddr, contractAddr, frozenBalance, res, SUCCESS, null);

    AccountCapsule newOwner = accountStore.get(contractAddr);
    Assert.assertEquals(oldOwner.getBalance() - frozenBalance, newOwner.getBalance());
    newOwner.setBalance(oldOwner.getBalance());
    if (res == 0) {
      Assert.assertEquals(
          oldOwner.getFrozenV2BalanceForBandwidth() + frozenBalance,
          newOwner.getFrozenV2BalanceForBandwidth());
      Assert.assertEquals(
          oldTotalNetWeight + frozenBalance / TRX_PRECISION, dynamicStore.getTotalNetWeight());
      Assert.assertEquals(oldTotalEnergyWeight, dynamicStore.getTotalEnergyWeight());
      Assert.assertEquals(oldTronPowerWeight, dynamicStore.getTotalTronPowerWeight());
    } else if (res == 1) {
      Assert.assertEquals(
          oldOwner.getFrozenV2BalanceForEnergy() + frozenBalance,
          newOwner.getFrozenV2BalanceForEnergy());
      Assert.assertEquals(oldTotalNetWeight, dynamicStore.getTotalNetWeight());
      Assert.assertEquals(oldTronPowerWeight, dynamicStore.getTotalTronPowerWeight());
      Assert.assertEquals(
          oldTotalEnergyWeight + frozenBalance / TRX_PRECISION,
          dynamicStore.getTotalEnergyWeight());
    } else {
      Assert.assertEquals(
          oldOwner.getTronPowerFrozenV2Balance() + frozenBalance,
          newOwner.getTronPowerFrozenV2Balance());
      Assert.assertEquals(oldTotalNetWeight, dynamicStore.getTotalNetWeight());
      Assert.assertEquals(oldTotalEnergyWeight, dynamicStore.getTotalEnergyWeight());
      Assert.assertEquals(
          oldTronPowerWeight + frozenBalance / TRX_PRECISION,
          dynamicStore.getTotalTronPowerWeight());
    }

    return result;
  }
```
