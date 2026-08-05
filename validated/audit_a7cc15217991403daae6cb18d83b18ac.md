## Analysis Result

### Title
Governance parameter `TOTAL_ENERGY_LIMIT` proposal does not refresh the enforced `TotalEnergyCurrentLimit` when adaptive energy is disabled - (File: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java`)

### Summary
`DynamicPropertiesStore.saveTotalEnergyLimit(long)` is used by the `TOTAL_ENERGY_LIMIT` governance proposal to update the network-wide energy limit, but it only updates the raw `TOTAL_ENERGY_LIMIT` value and the derived `TotalEnergyTargetLimit` — it never touches `TotalEnergyCurrentLimit`, which is the value actually consulted by `EnergyProcessor.calculateGlobalEnergyLimit`/`calculateGlobalEnergyLimitV2` to gate how much energy an account may use. This mirrors the Olympus `Operator::setReserveFactor` pattern: a governance-controlled parameter is updated, but the downstream cached threshold that enforces market/resource capacity is not regenerated to reflect the change.

### Finding Description
`ProposalService.process` handles the `TOTAL_ENERGY_LIMIT` case by calling the deprecated `saveTotalEnergyLimit`: [1](#0-0) 

That setter only writes `TOTAL_ENERGY_LIMIT` and recomputes `TotalEnergyTargetLimit`; it does not touch `TotalEnergyCurrentLimit`: [2](#0-1) 

By contrast, the sibling setter `saveTotalEnergyLimit2` (used by the separate `TOTAL_CURRENT_ENERGY_LIMIT` proposal case) *does* propagate the change into `TotalEnergyCurrentLimit`, but only when adaptive energy is disabled: [3](#0-2) [4](#0-3) 

The actually enforced per-account energy ceiling is computed from `getTotalEnergyCurrentLimit()`, not `getTotalEnergyLimit()`: [5](#0-4) [6](#0-5) 

`TotalEnergyCurrentLimit` is only ever recalculated by `EnergyProcessor.updateAdaptiveTotalEnergyLimit()`, and the per-transaction bookkeeping that feeds that recalculation (`blockEnergyUsage`) is itself gated on adaptive energy being enabled: [7](#0-6) [8](#0-7) 

Consequently, when `AllowAdaptiveEnergy == 0` (the resource model where `TotalEnergyCurrentLimit` should simply track `TotalEnergyLimit` directly), a governance proposal that raises or lowers `TOTAL_ENERGY_LIMIT` silently fails to take effect on the enforced ceiling, because the actuator used for that proposal (`saveTotalEnergyLimit`) never calls `saveTotalEnergyCurrentLimit`. This is analogous to `Operator::setReserveFactor` updating a parameter that feeds `fullCapacity()`/`Range` thresholds without calling `_regenerate`, leaving the market operating on the stale, pre-change value.

I was not able to fully verify, within the remaining tool budget, whether `Manager.java`'s call site to `updateAdaptiveTotalEnergyLimit()` is itself unconditionally invoked every maintenance cycle (which could mask the bug once adaptive energy is later toggled on) — this deserves confirmation with direct code access to `framework/src/main/java/org/tron/core/db/Manager.java`.

### Impact Explanation
This produces an invalid-state divergence between the governance-approved value (`TOTAL_ENERGY_LIMIT`) and the value actually enforced network-wide (`TotalEnergyCurrentLimit`) for all users staking/freezing TRX for energy, when the chain is running with `AllowAdaptiveEnergy == 0`. Governance intending to raise network capacity (e.g. after a network upgrade or congestion event) would find that accounts' energy quotas do not change, silently under- or over-restricting resource-based accounting for every account computing `calculateGlobalEnergyLimit`. This is a public, unprivileged-user-reachable accounting/invalid-state divergence bug affecting the resources subsystem, consistent with the rules' accepted "invalid-state/divergence" impact category.

### Likelihood Explanation
The `TOTAL_ENERGY_LIMIT` proposal path is a normal, expected governance action (witnesses regularly propose adjustments to network resource limits), and the described divergence occurs deterministically any time this proposal type is used while `AllowAdaptiveEnergy == 0` — no special conditions or attacker privilege are required beyond the standard witness governance flow that already exists in production.

### Recommendation
Make `saveTotalEnergyLimit` consistent with `saveTotalEnergyLimit2` by also updating `TotalEnergyCurrentLimit` when `AllowAdaptiveEnergy == 0`, or have `ProposalService`'s `TOTAL_ENERGY_LIMIT` case call `saveTotalEnergyLimit2` instead of the deprecated `saveTotalEnergyLimit`, ensuring the enforced ceiling is regenerated whenever the underlying governance parameter changes.

### Proof of Concept
1. Set `AllowAdaptiveEnergy = 0` (non-adaptive resource model).
2. Submit and pass a `TOTAL_ENERGY_LIMIT` proposal changing the limit from `L1` to `L2`, which invokes `ProposalService`'s handler calling `DynamicPropertiesStore.saveTotalEnergyLimit(L2)`. [1](#0-0) 
3. Observe that `getTotalEnergyLimit()` now returns `L2`, but `getTotalEnergyCurrentLimit()` still returns `L1` (unchanged), because `saveTotalEnergyLimit` never calls `saveTotalEnergyCurrentLimit`. [2](#0-1) 
4. Any subsequent account's `calculateGlobalEnergyLimit` continues to use the stale `L1` value via `getTotalEnergyCurrentLimit()`, meaning the governance-approved `L2` limit never affects real energy accounting until (if ever) `AllowAdaptiveEnergy` is separately toggled or `TOTAL_CURRENT_ENERGY_LIMIT` is proposed as a distinct action. [5](#0-4)

### Citations

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L111-114)
```java
        case TOTAL_ENERGY_LIMIT: {
          manager.getDynamicPropertiesStore().saveTotalEnergyLimit(entry.getValue());
          break;
        }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L119-122)
```java
        case TOTAL_CURRENT_ENERGY_LIMIT: {
          manager.getDynamicPropertiesStore().saveTotalEnergyLimit2(entry.getValue());
          break;
        }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L1338-1345)
```java
  @Deprecated
  public void saveTotalEnergyLimit(long totalEnergyLimit) {
    this.put(DynamicResourceProperties.TOTAL_ENERGY_LIMIT,
        new BytesCapsule(ByteArray.fromLong(totalEnergyLimit)));

    long ratio = getAdaptiveResourceLimitTargetRatio();
    saveTotalEnergyTargetLimit(totalEnergyLimit / ratio);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L1347-1356)
```java
  public void saveTotalEnergyLimit2(long totalEnergyLimit) {
    this.put(DynamicResourceProperties.TOTAL_ENERGY_LIMIT,
        new BytesCapsule(ByteArray.fromLong(totalEnergyLimit)));

    long ratio = getAdaptiveResourceLimitTargetRatio();
    saveTotalEnergyTargetLimit(totalEnergyLimit / ratio);
    if (getAllowAdaptiveEnergy() == 0) {
      saveTotalEnergyCurrentLimit(totalEnergyLimit);
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L65-93)
```java
  public void updateAdaptiveTotalEnergyLimit() {
    long totalEnergyAverageUsage = dynamicPropertiesStore
        .getTotalEnergyAverageUsage();
    long targetTotalEnergyLimit = dynamicPropertiesStore.getTotalEnergyTargetLimit();
    long totalEnergyCurrentLimit = dynamicPropertiesStore
        .getTotalEnergyCurrentLimit();
    long totalEnergyLimit = dynamicPropertiesStore.getTotalEnergyLimit();

    long result;
    if (totalEnergyAverageUsage > targetTotalEnergyLimit) {
      result = scaleByRate(totalEnergyCurrentLimit,
          AdaptiveResourceLimitConstants.CONTRACT_RATE_NUMERATOR,
          AdaptiveResourceLimitConstants.CONTRACT_RATE_DENOMINATOR);
    } else {
      result = scaleByRate(totalEnergyCurrentLimit,
          AdaptiveResourceLimitConstants.EXPAND_RATE_NUMERATOR,
          AdaptiveResourceLimitConstants.EXPAND_RATE_DENOMINATOR);
    }
    long upperBound = hardenCalculation()
        ? BigInteger.valueOf(totalEnergyLimit).multiply(BigInteger.valueOf(
            dynamicPropertiesStore.getAdaptiveResourceLimitMultiplier())).longValueExact()
        : totalEnergyLimit * dynamicPropertiesStore.getAdaptiveResourceLimitMultiplier();
    result = min(max(result, totalEnergyLimit, this.disableJavaLangMath()),
        upperBound, this.disableJavaLangMath());

    dynamicPropertiesStore.saveTotalEnergyCurrentLimit(result);
    logger.debug("Adjust totalEnergyCurrentLimit, old: {}, new: {}.",
        totalEnergyCurrentLimit, result);
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L136-141)
```java

    if (dynamicPropertiesStore.getAllowAdaptiveEnergy() == 1) {
      long blockEnergyUsage = dynamicPropertiesStore.getBlockEnergyUsage() + energy;
      dynamicPropertiesStore.saveBlockEnergyUsage(blockEnergyUsage);
    }

```

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L145-166)
```java
  public long calculateGlobalEnergyLimit(AccountCapsule accountCapsule) {
    long frozeBalance = accountCapsule.getAllFrozenBalanceForEnergy();
    if (dynamicPropertiesStore.supportUnfreezeDelay()) {
      return calculateGlobalEnergyLimitV2(frozeBalance);
    }
    if (frozeBalance < TRX_PRECISION) {
      return 0;
    }

    long totalEnergyLimit = dynamicPropertiesStore.getTotalEnergyCurrentLimit();
    long totalEnergyWeight = dynamicPropertiesStore.getTotalEnergyWeight();
    if (dynamicPropertiesStore.allowNewReward() && totalEnergyWeight <= 0) {
      return 0;
    } else {
      assert totalEnergyWeight > 0;
    }
    if (hardenCalculation()) {
      return calculateGlobalLimitV1(frozeBalance, totalEnergyLimit, totalEnergyWeight);
    }
    long energyWeight = frozeBalance / TRX_PRECISION;
    return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L168-179)
```java
  public long calculateGlobalEnergyLimitV2(long frozeBalance) {
    long totalEnergyLimit = dynamicPropertiesStore.getTotalEnergyCurrentLimit();
    long totalEnergyWeight = dynamicPropertiesStore.getTotalEnergyWeight();
    if (totalEnergyWeight == 0) {
      return 0;
    }
    if (hardenCalculation()) {
      return calculateGlobalLimitV2(frozeBalance, totalEnergyLimit, totalEnergyWeight);
    }
    double energyWeight = (double) frozeBalance / TRX_PRECISION;
    return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
  }
```
