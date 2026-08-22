### Title
Unchecked `totalEnergyWeight` division-by-zero in `RepositoryImpl.calculateGlobalEnergyLimit` - (File: actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java)

### Summary
This is an analog of the reported bug class: a reward/resource accounting value is used as a divisor without a runtime guard, relying only on an assumption (in the original report, that `contract_weights` is nonzero after clearing state; here, that `totalEnergyWeight` is always positive), which can throw an exception and revert/DoS the caller when the invariant does not hold.

### Finding Description
`RepositoryImpl.calculateGlobalEnergyLimit` computes an account's energy limit as a function of `totalEnergyWeight`, the network-wide amount of TRX frozen for energy: [1](#0-0) 

The method only guards against `frozeBalance < TRX_PRECISION` (line 969), but performs no actual runtime check on `totalEnergyWeight` before using it as a divisor. Instead it relies on a Java `assert totalEnergyWeight > 0;` statement at line 976. Java `assert` statements are disabled by default at runtime (they require the JVM to be started with `-ea`), so in a standard production node this assertion is compiled out and provides zero protection. If `totalEnergyWeight` is `0` (e.g., on networks/private chains before any TRX has been frozen for energy, or during edge-case bootstrapping), both branches of the calculation divide by zero:
- The hardened path (`hardenResourceCalculation()` true) calls `BigInteger.valueOf(totalEnergyLimit).divide(BigInteger.valueOf(0))`, which throws `ArithmeticException: BigInteger divide by zero`.
- The non-hardened path performs a `double` division `(double) totalEnergyLimit / totalEnergyWeight`, which produces `Infinity`/`NaN` rather than throwing, silently corrupting the computed energy limit instead of crashing.

This contrasts with the equivalent, correctly-guarded method in the chain-base resource processor, `EnergyProcessor.calculateGlobalEnergyLimit`, which explicitly checks and returns `0` when `totalEnergyWeight <= 0`: [2](#0-1) 

`RepositoryImpl.calculateGlobalEnergyLimit` is the TVM-repository equivalent used during smart contract execution (native contract processors / TVM freeze-v2 resource logic), so it is reachable from ordinary broadcast transactions that trigger TVM execution paths involving energy-limit computation for an account with frozen energy balance, exactly mirroring the farm-manager pattern of dividing by a shared/global accounting value without validating it at the division site.

### Impact Explanation
If reached while `totalEnergyWeight` is `0` and the hardened resource-calculation path is enabled, the call throws an uncaught `ArithmeticException`, which can abort/crash processing of the transaction or block-execution path that invoked it — a denial-of-service on the node processing path for that account/contract call. If the hardened path is disabled, the computation silently returns `Infinity`/garbage energy-limit values, which could corrupt energy accounting and resource metering rather than fail cleanly. Either outcome corresponds to the flagged bug class: an accounting denominator assumed non-zero by convention (rather than defensively checked) that is reachable from normal protocol operations.

### Likelihood Explanation
`totalEnergyWeight` is normally kept non-zero on live networks because TRX is continuously frozen for energy, so on Mainnet this is unlikely to trigger under ordinary conditions. However, on freshly bootstrapped/private/test chains, or any state where all previously-frozen energy balances are fully unfrozen, `totalEnergyWeight` can legitimately become `0`, and this code path is invoked as part of standard TVM/native-contract resource-limit computation, not through any privileged action. The reliance on a compiled-out `assert` instead of an explicit guard is the same root-cause pattern as the referenced report (checked at neither the true source of the invariant nor the site of division).

### Recommendation
Add an explicit runtime check in `RepositoryImpl.calculateGlobalEnergyLimit` (mirroring `EnergyProcessor.calculateGlobalEnergyLimit`) to return `0` (or otherwise short-circuit) when `totalEnergyWeight <= 0`, instead of relying on an `assert` statement, and ensure the same is applied consistently across all remaining resource-limit calculation methods that divide by `totalEnergyWeight`/`totalNetWeight`.

### Proof of Concept
Not independently executed; concluded from static analysis of `RepositoryImpl.calculateGlobalEnergyLimit` (lines 967-985) compared against the guarded twin implementation `EnergyProcessor.calculateGlobalEnergyLimit` (lines 145-166), which demonstrates the missing-guard pattern is intentional elsewhere in the codebase but absent here. Dynamic confirmation (e.g., constructing a chain state with `totalEnergyWeight == 0` and `allowHardenResourceCalculation` enabled, then invoking a native-contract/TVM path that calls `calculateGlobalEnergyLimit` for an account with frozen energy balance) was not performed due to lack of runtime/test execution access in this analysis session.

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
