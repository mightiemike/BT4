## Title
Division by zero in `RepositoryImpl.calculateGlobalEnergyLimit()` via unguarded `totalEnergyWeight` (relies on a disabled Java `assert`) - (File: `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java`)

### Summary
`RepositoryImpl.calculateGlobalEnergyLimit()` divides by `totalEnergyWeight` to compute a caller's/creator's frozen-energy limit for TVM execution, but instead of returning a safe default when `totalEnergyWeight` is 0 (as the parallel implementations do), it only has an `assert totalEnergyWeight > 0;`, which is a no-op in production JVMs (assertions are disabled by default, no `-ea` flag). This mirrors the reported bug class: a denominator that is assumed non-zero but is not actually enforced, which can freeze/DoS the dependent computation for all callers.

### Finding Description
`calculateGlobalEnergyLimit()` in `RepositoryImpl.java` reads `totalEnergyWeight` from `DynamicPropertiesStore` and uses it as a divisor without a real guard: [1](#0-0) 

Note the `assert totalEnergyWeight > 0;` at line 976 — Java assertions are stripped at runtime unless the JVM is launched with `-ea`, so this provides no actual protection in a production deployment. If `totalEnergyWeight` were ever 0, the hardened path (`BigInteger... .divide(BigInteger.valueOf(totalEnergyWeight)).longValueExact()`) throws `ArithmeticException: / by zero`, and the non-hardened path (`(double) totalEnergyLimit / totalEnergyWeight`) silently produces `Infinity`/`NaN`, corrupting the energy-limit computation.

This is inconsistent with sibling implementations that perform the equivalent computation but *do* guard the same denominator with a real runtime check:
- `EnergyProcessor.calculateGlobalEnergyLimit()`: `if (dynamicPropertiesStore.allowNewReward() && totalEnergyWeight <= 0) { return 0; } else { assert totalEnergyWeight > 0; }` [2](#0-1) 
- `BandwidthProcessor.calculateGlobalNetLimit()`: `if (totalNetWeight == 0) { return 0; }` [3](#0-2) 

`RepositoryImpl.calculateGlobalEnergyLimit()` lacks the equivalent `if (totalEnergyWeight <= 0) return 0;` real check, relying solely on the disabled assertion.

This method is reachable from ordinary contract execution: `VMActuator.getAccountEnergyLimitWithFloatRatio()` calls `rootRepository.calculateGlobalEnergyLimit(account)` whenever the caller/creator has a non-zero `totalBalanceForEnergyFreeze`, as part of computing energy available for any `TriggerSmartContract`/`CreateSmartContract` transaction: [4](#0-3) 

### Impact Explanation
If the network-wide `totalEnergyWeight` ever reaches 0 (e.g., through the resource/delegation lifecycle, network migrations, or a state where energy-freeze accounting nets out to zero), every smart-contract call/creation that routes through `getAccountEnergyLimitWithFloatRatio` → `RepositoryImpl.calculateGlobalEnergyLimit()` will either throw an unhandled `ArithmeticException` (hardened-calculation path) — causing transaction execution/validation to fail unexpectedly, effectively a DoS on contract calls network-wide — or silently return `Infinity`/`NaN` in the legacy double-math path, corrupting energy accounting for the affected account. This is a resource-accounting/DoS class issue in the TVM execution path, reachable by any broadcast smart-contract transaction, not requiring a privileged actor.

### Likelihood Explanation
Likelihood is low under normal network operation since `totalEnergyWeight` is expected to stay positive as long as any account has frozen/delegated balance for energy, similar to how the original veSupply[weekCursor] bug required a specific, unusual protocol state. However, the missing real guard (relying on assertions disabled in production) is a genuine code defect that the analogous functions elsewhere in the same codebase (`EnergyProcessor`, `BandwidthProcessor`) already treat as worth explicitly guarding against, indicating the maintainers recognize this as a reachable edge case.

### Recommendation
Replace the `assert totalEnergyWeight > 0;` in `RepositoryImpl.calculateGlobalEnergyLimit()` with an explicit runtime check that returns a safe default (e.g., `0`) when `totalEnergyWeight <= 0`, consistent with `EnergyProcessor.calculateGlobalEnergyLimit()` and `BandwidthProcessor.calculateGlobalNetLimit()`.

### Proof of Concept
1. Drive (or simulate in test harness) `DynamicPropertiesStore` state to `totalEnergyWeight == 0` (e.g., all energy-freeze delegations fully unwound).
2. Submit any `TriggerSmartContract` transaction from/against an account with `getAllFrozenBalanceForEnergy() >= TRX_PRECISION`.
3. Execution reaches `VMActuator.getAccountEnergyLimitWithFloatRatio()` → `rootRepository.calculateGlobalEnergyLimit(account)`.
4. With `VMConfig.allowHardenResourceCalculation()` enabled, `BigInteger.divide(BigInteger.ZERO)` throws `ArithmeticException`, aborting execution unexpectedly for a well-formed transaction (DoS); without hardening, the double-division silently yields `Infinity`, corrupting the returned energy limit.

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

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L606-631)
```java
  private long getAccountEnergyLimitWithFloatRatio(AccountCapsule account, long feeLimit,
      long callValue) {

    long sunPerEnergy = VMConstant.SUN_PER_ENERGY;
    if (rootRepository.getDynamicPropertiesStore().getEnergyFee() > 0) {
      sunPerEnergy = rootRepository.getDynamicPropertiesStore().getEnergyFee();
    }
    // can change the calc way
    long leftEnergyFromFreeze = rootRepository.getAccountLeftEnergyFromFreeze(account);
    callValue = max(callValue, 0, VMConfig.disableJavaLangMath());
    long energyFromBalance = floorDiv(max(
        account.getBalance() - callValue, 0, VMConfig.disableJavaLangMath()), sunPerEnergy,
        VMConfig.disableJavaLangMath());

    long energyFromFeeLimit;
    long totalBalanceForEnergyFreeze = account.getAllFrozenBalanceForEnergy();
    if (0 == totalBalanceForEnergyFreeze) {
      energyFromFeeLimit =
          feeLimit / sunPerEnergy;
    } else {
      long totalEnergyFromFreeze = rootRepository
          .calculateGlobalEnergyLimit(account);
      long leftBalanceForEnergyFreeze = getEnergyFee(totalBalanceForEnergyFreeze,
          leftEnergyFromFreeze,
          totalEnergyFromFreeze);

```
