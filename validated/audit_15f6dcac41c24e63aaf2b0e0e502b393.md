Based on my analysis, I found a concrete analog in java-tron: an inconsistency in the `calculateGlobalEnergyLimit` computation used to bound TVM energy for a smart-contract caller, where one call path (used inside actual contract execution via the `Repository` interface) is missing the zero-weight guard that exists in the parallel, non-VM code path.

### Title
Unguarded division by `totalEnergyWeight`/`totalNetWeight` in resource-limit calculation can throw/produce undefined results during TVM contract execution - (File: `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java`)

### Summary
`VaderPool`'s `GasThrottle` gates every swap on an externally-derived value (`_FAST_GAS_ORACLE`); if that value becomes `0`, every swap fails with no recovery path because no code defends against the zero case and no admin switch exists to disable the check. The java-tron analog is `RepositoryImpl.calculateGlobalEnergyLimit`, which gates the energy limit available to a smart-contract caller on the on-chain state value `totalEnergyWeight`. Unlike the equivalent computation in `EnergyProcessor.calculateGlobalEnergyLimit` and `BandwidthProcessor.calculateGlobalNetLimit`/`calculateGlobalNetLimitV2`, this VM-facing path has no explicit `totalEnergyWeight == 0` short-circuit — only a Java `assert`, which is a no-op in production JVMs (assertions are disabled by default).

### Finding Description
`RepositoryImpl.calculateGlobalEnergyLimit` (called from `VMActuator`/`Program` during TVM contract calls to size the caller's available energy) computes: [1](#0-0) 

Note the guard at line 976, `assert totalEnergyWeight > 0;`, is the only protection before dividing by `totalEnergyWeight` in both the "hardened" `BigInteger` path (`.divide(BigInteger.valueOf(totalEnergyWeight))`) and the legacy double-arithmetic path. Java assertions are disabled unless the JVM is started with `-ea`, which is not the case for production `java-tron` deployments, so this check is effectively dead code in production.

Contrast this with the equivalent, non-VM computation in `EnergyProcessor.calculateGlobalEnergyLimit`, which explicitly returns `0` when `totalEnergyWeight <= 0` under `allowNewReward()`: [2](#0-1) 

and `BandwidthProcessor.calculateGlobalNetLimit`/`calculateGlobalNetLimitV2`, which both explicitly check `totalNetWeight == 0`: [3](#0-2) 

`totalEnergyWeight` is maintained via `DynamicPropertiesStore.addTotalEnergyWeight`, which under `allowNewReward()` is floored at `0`: [4](#0-3) 

meaning `totalEnergyWeight == 0` is a reachable, on-chain state (e.g., all frozen-for-energy balances being unfrozen network-wide, or transiently through freeze/unfreeze/delegate bookkeeping), not merely theoretical.

### Impact Explanation
When `totalEnergyWeight` is `0` and `VMConfig.allowHardenResourceCalculation()` is enabled, `RepositoryImpl.calculateGlobalEnergyLimit`'s `BigInteger.divide(BigInteger.valueOf(0))` throws an uncaught `ArithmeticException: / by zero` for any TVM call made by a caller account whose frozen-for-energy balance is `>= 1 TRX` (`frozeBalance >= TRX_PRECISION` at line 969). This computation sits inside contract-call energy accounting invoked from `VMActuator`/`Program`, so an uncaught exception here propagates through transaction execution, producing invalid/inconsistent transaction results (or crashing execution) rather than a clean, deterministic `ContractValidateException`/`AccountResourceInsufficientException` as intended. Because the same value is computed inconsistently (guarded in `EnergyProcessor`/`BandwidthProcessor`, unguarded in the VM-facing `RepositoryImpl`), nodes/paths that hit the VM route versus the resource-consumption route can diverge in behavior for the same on-chain state, matching the impact class of invalid-state/halt behavior. As with `GasThrottle`, there is no governance switch to bypass this specific unguarded division once triggered — only toggling `allowHardenResourceCalculation` (a chain parameter) would change which arithmetic path is taken, and the legacy double-arithmetic branch masks the problem with undefined floating-point behavior (`Infinity`/`NaN` cast to `long`) rather than fixing it.

### Likelihood Explanation
Reaching `totalEnergyWeight == 0` requires the aggregate frozen-for-energy balance across the whole network to be zero, which is unlikely in a live network with active resource freezing, but is not merely theoretical — it is a reachable state through the ordinary, unprivileged `FreezeBalanceContract`/`UnfreezeBalanceContract` flows that maintain this counter, and is explicitly exercised by test code (`CalculateGlobalLimitHardenTest`, `RepositoryImplHardenTest`) covering the division-by-zero/overflow behavior of the "hardened" calculation. The gap is specifically the missing defensive check in the VM-facing copy of the calculation compared to its two sibling implementations, indicating an inconsistency introduced when the "hardened" `BigInteger` division was added without carrying over the zero-guard.

### Recommendation
Add the same `totalEnergyWeight <= 0` (and, for symmetry, `totalNetWeight`/`totalTronPowerWeight`) short-circuit to `RepositoryImpl.calculateGlobalEnergyLimit` that already exists in `EnergyProcessor.calculateGlobalEnergyLimit` and `BandwidthProcessor.calculateGlobalNetLimit`, returning `0` instead of relying on a production-inert `assert` before dividing.

### Proof of Concept
1. Drive `totalEnergyWeight` in `DynamicPropertiesStore` to `0` via ordinary unfreeze operations network-wide (or in a test harness, call `dbManager.getDynamicPropertiesStore().saveTotalEnergyWeight(0)` directly, as done in `CalculateGlobalLimitHardenTest`).
2. Enable `VMConfig.allowHardenResourceCalculation()` (a supported chain parameter).
3. Trigger a smart contract call (`TriggerSmartContract`) from an account whose `getAllFrozenBalanceForEnergy() >= TRX_PRECISION`, causing `VMActuator`/`Program` to invoke `RepositoryImpl.calculateGlobalEnergyLimit` at [1](#0-0) .
4. Observe `BigInteger.valueOf(totalEnergyWeight)` being `0`, causing `.divide(...)` to throw `ArithmeticException`, uncaught by any guard in this method (the `assert` at line 976 is a no-op without `-ea`), unlike the identical scenario handled safely in `EnergyProcessor`/`BandwidthProcessor`.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L967-985)
```java
  public long calculateGlobalEnergyLimit(AccountCapsule accountCapsule) {
    long frozeBalance = accountCapsule.getAllFrozenBalanceForEnergy();
    if (frozeBalance < TRX_PRECISION) {
      return 0;
    }
    long energyWeight = frozeBalance / TRX_PRECISION;
    long totalEnergyLimit = getDynamicPropertiesStore().getTotalEnergyCurrentLimit();
    long totalEnergyWeight = getDynamicPropertiesStore().getTotalEnergyWeight();

    assert totalEnergyWeight > 0;

    if (hardenResourceCalculation()) {
      return BigInteger.valueOf(energyWeight)
          .multiply(BigInteger.valueOf(totalEnergyLimit))
          .divide(BigInteger.valueOf(totalEnergyWeight))
          .longValueExact();
    }
    return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
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

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L432-466)
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

  public long calculateGlobalNetLimitV2(long frozeBalance) {
    long totalNetLimit = dynamicPropertiesStore.getTotalNetLimit();
    long totalNetWeight = dynamicPropertiesStore.getTotalNetWeight();
    if (totalNetWeight == 0) {
      return 0;
    }
    if (hardenCalculation()) {
      return calculateGlobalLimitV2(frozeBalance, totalNetLimit, totalNetWeight);
    }
    double netWeight = (double) frozeBalance / TRX_PRECISION;
    return (long) (netWeight * ((double) totalNetLimit / totalNetWeight));
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L2282-2293)
```java
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
```
