### Title
Missing floor-clamp on global resource weight accounting in TVM-native-contract path causes divergence from actuator path - ([File: actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java])

### Summary
The external report describes a Solidity `GaugeController._changeGaugeWeight` bug where unguarded subtraction/addition on weight totals underflows, preventing a weight from ever being reduced/zeroed and corrupting the aggregate `totalWeight`. The closest reachable analog in java-tron is the accounting of the network-wide resource weight totals (`TOTAL_NET_WEIGHT`, `TOTAL_ENERGY_WEIGHT`, `TOTAL_TRON_POWER_WEIGHT`), which are mutated from two independent code paths with **inconsistent guards**: one path floors the result at zero, the other does not.

### Finding Description
`DynamicPropertiesStore.addTotalNetWeight/addTotalEnergyWeight/addTotalTronPowerWeight` clamp the result to a minimum of `0` when `allowNewReward()` is enabled: [1](#0-0) 

This guarded method is called directly by classic actuators such as `FreezeBalanceActuator`/`UnfreezeBalanceActuator`: [2](#0-1) 

However, `RepositoryImpl` — the `Repository` implementation used by all **TVM native contract processors** (`UnfreezeBalanceV2Processor`, `UnDelegateResourceProcessor`, `DelegateResourceProcessor`, `UnfreezeBalanceProcessor`) — implements the same-named methods **without any floor/guard**: [3](#0-2) 

Both code paths read and write the exact same underlying keys (`TOTAL_NET_WEIGHT`, `TOTAL_ENERGY_WEIGHT`, `TOTAL_TRON_POWER_WEIGHT`) in the same store, so the shared global accounting state can be pushed to zero via one path but driven further negative via the other, exactly analogous to the reported bug where the code path that should prevent a weight/sum from going below a valid floor fails to do so due to a missing guard on the arithmetic.

The unguarded processors are directly reachable by unprivileged callers through TVM: any smart contract can invoke `unfreezeBalanceV2`, `undelegateResource`, `delegateResource`, etc. via TVM precompiles, going through `UnDelegateResourceProcessor`/`UnfreezeBalanceV2Processor`, which call `repo.addTotalNetWeight(...)`/`repo.addTotalEnergyWeight(...)` on `RepositoryImpl` with no floor: [4](#0-3) [5](#0-4) 

Crucially, `getTotalNetWeight()`/`getTotalEnergyWeight()` are used as **divisors** in per-account resource-usage transfer calculations during undelegate, which is itself unprivileged-user reachable: [6](#0-5) [7](#0-6) 

### Impact Explanation
If the TVM-native path drives the global weight below zero (bypassing the floor that the classic actuator path enforces), the shared `TOTAL_NET_WEIGHT`/`TOTAL_ENERGY_WEIGHT` values used network-wide to compute per-account bandwidth/energy limits and per-account usage-transfer ratios (`getTotalNetLimit() / getTotalNetWeight()`) become corrupted (zero, negative, or wildly skewed via double-division). This is an invalid-state/divergence in a core accounting value shared by every account's resource entitlement, and could translate into incorrect (potentially unbounded/underpriced) bandwidth or energy allowances — an underpriced-public-work condition — or division-by-zero/NaN propagation corrupting usage transfer math on undelegate.

### Likelihood Explanation
Exploitation requires driving the aggregate weight to a state where the unguarded `RepositoryImpl` path decrements it below the floor that the guarded `DynamicPropertiesStore` path would otherwise enforce. This is plausible under normal usage patterns (freeze via actuator, unfreeze/undelegate via TVM contract, or vice versa) because both paths operate on the same persisted key with different invariants; however, I could not fully confirm from static review alone whether real-world freeze/unfreeze amounts in practice ever cause the total to dip below the floor bound in the unguarded path (this would require dynamic/fuzz testing to confirm a concrete negative-total scenario), and `allowNewReward()`'s exact activation conditions across the codebase were not conclusively enumerated within available search results.

### Recommendation
Apply the same `max(0, totalWeight)` floor guard (gated identically by `allowNewReward()`) inside `RepositoryImpl.addTotalNetWeight`, `addTotalEnergyWeight`, and `addTotalTronPowerWeight`, mirroring `DynamicPropertiesStore`'s implementation, so both code paths enforce identical invariants on the shared global weight state.

### Proof of Concept
1. Enable `allowNewReward` (or confirm it is enabled on the target network).
2. Freeze/delegate balance through the standard actuator path (guarded, floors at 0 on unfreeze).
3. Freeze an equivalent amount through a smart contract calling the TVM native `freezeBalanceV2`/`delegateResource` precompile, then unfreeze/undelegate it through the TVM path (`UnfreezeBalanceV2Processor`/`UnDelegateResourceProcessor`), which calls `RepositoryImpl.addTotalNetWeight`/`addTotalEnergyWeight` without a floor.
4. Interleave freeze (actuator path, adds to the guarded total) and unfreeze/undelegate (TVM path, subtracts via the unguarded total) operations such that the aggregate should approach zero; because the TVM path never clamps, further decrements can push the shared value negative.
5. Observe `getTotalNetWeight()`/`getTotalEnergyWeight()` returning zero/negative, and downstream divisions in `UnDelegateResourceActuator`/`UnDelegateResourceProcessor` (`getTotalNetLimit() / getTotalNetWeight()`) producing corrupted/undefined resource-limit values.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L2270-2306)
```java
  public void addTotalNetWeight(long amount) {
    if (amount == 0) {
      return;
    }
    long totalNetWeight = getTotalNetWeight();
    totalNetWeight += amount;
    if (allowNewReward()) {
      totalNetWeight = max(0, totalNetWeight, disableJavaLangMath());
    }
    saveTotalNetWeight(totalNetWeight);
  }

  //The unit is trx
  public void addTotalEnergyWeight(long amount) {
    if (amount == 0) {
      return;
    }
    long totalEnergyWeight = getTotalEnergyWeight();
    totalEnergyWeight += amount;
    if (allowNewReward()) {
      totalEnergyWeight = max(0, totalEnergyWeight, disableJavaLangMath());
    }
    saveTotalEnergyWeight(totalEnergyWeight);
  }

  //The unit is trx
  public void addTotalTronPowerWeight(long amount) {
    if (amount == 0) {
      return;
    }
    long totalWeight = getTotalTronPowerWeight();
    totalWeight += amount;
    if (allowNewReward()) {
      totalWeight = max(0, totalWeight, disableJavaLangMath());
    }
    saveTotalTronPowerWeight(totalWeight);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L134-150)
```java
  private void addTotalWeight(ResourceCode resourceCode, DynamicPropertiesStore dynamicStore,
                              long frozenBalance, long increment) {
    long weight = dynamicStore.allowNewReward() ? increment : frozenBalance / TRX_PRECISION;
    switch (resourceCode) {
      case BANDWIDTH:
        dynamicStore.addTotalNetWeight(weight);
        break;
      case ENERGY:
        dynamicStore.addTotalEnergyWeight(weight);
        break;
      case TRON_POWER:
        dynamicStore.addTotalTronPowerWeight(weight);
        break;
      default:
        logger.debug("Resource Code Error.");
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L1165-1186)
```java
  //The unit is trx
  @Override
  public void addTotalNetWeight(long amount) {
    long totalNetWeight = getTotalNetWeight();
    totalNetWeight += amount;
    saveTotalNetWeight(totalNetWeight);
  }

  //The unit is trx
  @Override
  public void addTotalEnergyWeight(long amount) {
    long totalEnergyWeight = getTotalEnergyWeight();
    totalEnergyWeight += amount;
    saveTotalEnergyWeight(totalEnergyWeight);
  }

  @Override
  public void addTotalTronPowerWeight(long amount) {
    long totalTronPowerWeight = getTotalTronPowerWeight();
    totalTronPowerWeight += amount;
    saveTotalTronPowerWeight(totalTronPowerWeight);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L178-204)
```java
  public void updateTotalResourceWeight(AccountCapsule accountCapsule,
                                        Common.ResourceCode freezeType,
                                        long unfreezeBalance,
                                        Repository repo) {
    switch (freezeType) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(-unfreezeBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        repo.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(-unfreezeBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        repo.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(-unfreezeBalance);
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        repo.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
        //this should never happen
        break;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L115-123)
```java
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * dynamicStore.getTotalNetLimit() / repo.getTotalNetWeight());
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage, VMConfig.disableJavaLangMath());

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L160-193)
```java
    // modify owner Account
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, false);
    DelegatedResourceCapsule delegatedResourceCapsule = repo.getDelegatedResource(key);
    switch (param.getResourceType()) {
      case BANDWIDTH: {
        delegatedResourceCapsule.addFrozenBalanceForBandwidth(-unDelegateBalance, 0);

        ownerCapsule.addDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
        ownerCapsule.addFrozenBalanceForBandwidthV2(unDelegateBalance);

        BandwidthProcessor processor = new BandwidthProcessor(ChainBaseManager.getInstance());
        if (Objects.nonNull(receiverCapsule) && transferUsage > 0) {
          processor.unDelegateIncrease(ownerCapsule, receiverCapsule,
              transferUsage, BANDWIDTH, now);
        }
      }
      break;
      case ENERGY: {
        delegatedResourceCapsule.addFrozenBalanceForEnergy(-unDelegateBalance, 0);

        ownerCapsule.addDelegatedFrozenV2BalanceForEnergy(-unDelegateBalance);
        ownerCapsule.addFrozenBalanceForEnergyV2(unDelegateBalance);

        EnergyProcessor processor =
            new EnergyProcessor(dynamicStore, ChainBaseManager.getInstance().getAccountStore());
        if (Objects.nonNull(receiverCapsule) && transferUsage > 0) {
          processor.unDelegateIncrease(ownerCapsule, receiverCapsule, transferUsage, ENERGY, now);
        }
      }
      break;
      default:
        //this should never happen
        break;
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L80-88)
```java
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalNetLimit()) / dynamicStore.getTotalNetWeight()));
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }
```
