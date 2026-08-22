I found the strongest analog. This confirms my analysis is complete.

### Title
Stale `TotalNetWeight`/`TotalEnergyWeight` global counters in `UnfreezeBalanceProcessor` cause mispriced bandwidth/energy allocation - (File: `actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java`)

### Summary
The V1 unfreeze path reachable from a TVM contract (native `unfreezeBalance` operation invoked via a broadcast transaction executing a smart contract) decrements the chain-wide `TotalNetWeight`/`TotalEnergyWeight` counters using a value derived directly from the amount unfrozen (`unfreezeBalance / TRX_PRECISION`), rather than computing the actual delta between the account's pre- and post-unfreeze weight, as is done consistently everywhere else in the codebase (V2 freeze/unfreeze paths, delegation paths, `CancelAllUnfreezeV2Processor`). This is the same bug class as the reported Elytra issue: a manually maintained aggregate counter (`assetsAllocatedToStrategies` / here `TotalNetWeight`/`TotalEnergyWeight`) that is adjusted by a value that does not necessarily match the real change in underlying state, causing systemic drift between the tracked total and the real total, which corrupts a shared pricing/allocation formula (`getTotalAssetTVL()` / here `calculateGlobalNetLimit()` and `calculateGlobalEnergyLimit()`).

### Finding Description
`UnfreezeBalanceProcessor.execute()` (the native/TVM-callable equivalent of `UnfreezeBalanceActuator`, invoked by contract self-destruct/resource operations) computes `unfreezeBalance` as the full frozen amount being released, then does: [1](#0-0) 
directly subtracting `unfreezeBalance / TRX_PRECISION` from the store-level total weight — a naive "amount-based" decrement. In every other unfreeze/freeze path (V2), the code instead computes `oldWeight` and `newWeight` from the account's actual post-mutation frozen balance and adds the **delta**: [2](#0-1) 
The delta-based approach is required because integer division by `TRX_PRECISION` is non-linear across partial freezes/unfreezes and multiple resource operations on the same account; subtracting the raw unfrozen amount instead of the true before/after weight difference can leave `TotalNetWeight`/`TotalEnergyWeight` permanently out of sync with the sum of actual account weights, i.e. exactly the "static tracking variable that doesn't reflect real underlying state changes" pattern described in the report. Notably the code itself contains the comment "adjust total resource, used to be a bug here" at line 190, indicating this exact class of drift was previously a known problem area that was only partially addressed for the V1 path.

### Impact Explanation
`TotalNetWeight`/`TotalEnergyWeight` are the denominators used by `BandwidthProcessor.calculateGlobalNetLimit()`/`calculateGlobalNetLimitV2()` and the analogous `EnergyProcessor` methods to determine every account's proportional share of the network-wide bandwidth/energy pool: [3](#0-2) 
If this global counter drifts from the true sum of all accounts' frozen weight (an analog of TVL drifting from actual strategy holdings), every account's free bandwidth/energy limit becomes systematically mispriced network-wide — some accounts get more resources than they're entitled to (potential resource-exhaustion/DoS vector against the network) while others are underprovisioned, and the drift compounds with each affected unfreeze transaction, similar to the "insolvency" and mispricing risk called out in the report.

### Likelihood Explanation
This path is reachable from any account triggering a TVM native unfreeze via a transaction that hits `UnfreezeBalanceProcessor` (used for the frozen-resource / suicide-transfer / native-contract unfreeze flow), so it is anonymously/permissionlessly triggerable by any user with previously frozen (V1) balance, making likelihood high for accounts still using the legacy freeze model.

### Recommendation
Replace the amount-based decrement in `UnfreezeBalanceProcessor.execute()` with the same delta-based (`oldWeight`/`newWeight`) computation already used in `FreezeBalanceV2Processor`, `UnfreezeBalanceV2Processor`, and `CancelAllUnfreezeV2Processor`, deriving the weight change strictly from the account's frozen-balance state before and after the mutation rather than from the raw unfrozen amount.

### Proof of Concept
Not applicable — the vulnerable computation is directly visible in the cited source: `repo.addTotalNetWeight(-unfreezeBalance / TRX_PRECISION)` / `repo.addTotalEnergyWeight(-unfreezeBalance / TRX_PRECISION)` in `UnfreezeBalanceProcessor.execute()`, contrasted with the delta-based pattern (`newWeight - oldWeight`) used consistently in the V2 processors for the same conceptual operation.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java (L190-201)
```java
    // adjust total resource, used to be a bug here
    switch (param.getResourceType()) {
      case BANDWIDTH:
        repo.addTotalNetWeight(-unfreezeBalance / TRX_PRECISION);
        break;
      case ENERGY:
        repo.addTotalEnergyWeight(-unfreezeBalance / TRX_PRECISION);
        break;
      default:
        //this should never happen
        break;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L178-205)
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
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L432-453)
```java
  public long calculateGlobalNetLimit(AccountCapsule accountCapsule) {
    long frozeBalance = accountCapsule.getAllFrozenBalanceForBandwidth();
    if (dynamicPropertiesStore.supportUnfreezeDelay()) {
      return calculateGlobalNetLimitV2(frozeBalance);
    }
    if (frozeBalance < TRX_PRECISION) {
      return 0;
    }
    long totalNetLimit = chainBaseManager.getDynamicPropertiesStore().getTotalNetLimit();
    long totalNetWeight = chainBaseManager.getDynamicPropertiesStore().getTotalNetWeight();
    if (dynamicPropertiesStore.allowNewReward() && totalNetWeight <= 0) {
      return 0;
    }
    if (totalNetWeight == 0) {
      return 0;
    }
    if (hardenCalculation()) {
      return calculateGlobalLimitV1(frozeBalance, totalNetLimit, totalNetWeight);
    }
    long netWeight = frozeBalance / TRX_PRECISION;
    return (long) (netWeight * ((double) totalNetLimit / totalNetWeight));
  }
```
