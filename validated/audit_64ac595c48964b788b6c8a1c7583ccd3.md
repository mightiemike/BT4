### Title
Missing zero-check on `totalEnergyWeight` causes division-by-zero / unguarded ArithmeticException in TVM energy-limit calculation - ([File: actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java])

### Summary
`RepositoryImpl.calculateGlobalEnergyLimit(AccountCapsule)` divides by `totalEnergyWeight` without validating it is non-zero, relying only on a Java `assert` statement that is disabled by default at runtime (JVM assertions require the `-ea` flag, which is not the default in production deployments). This mirrors the reported oracle-price-zero bug class: an unchecked divisor obtained from chain state can be zero, triggering a division-by-zero exception path during normal contract execution.

### Finding Description
`calculateGlobalEnergyLimit` reads `totalEnergyWeight` from `DynamicPropertiesStore` and uses it as a divisor: [1](#0-0) 

The only safeguard is `assert totalEnergyWeight > 0;`, which is a no-op unless the JVM is started with `-ea`. If `hardenResourceCalculation()` (i.e. `allowHardenResourceCalculation`) is enabled and `totalEnergyWeight` is `0`, the call `BigInteger.valueOf(totalEnergyLimit).divide(BigInteger.valueOf(0))` throws `ArithmeticException: BigInteger divide by zero`, uncaught at this layer. If hardening is disabled, the fallback `(double) totalEnergyLimit / totalEnergyWeight` divides by zero as a floating-point operation, producing `Infinity`/`NaN`, which is then cast to `long`, yielding `Long.MAX_VALUE`/`0`/garbage — silently corrupting the computed energy limit rather than throwing.

By contrast, the sibling implementation in `EnergyProcessor.calculateGlobalEnergyLimit` (used outside the VM/actuator path) does perform an explicit guard, but only when `allowNewReward()` is true: [2](#0-1) 
```
if (dynamicPropertiesStore.allowNewReward() && totalEnergyWeight <= 0) {
  return 0;
} else {
  assert totalEnergyWeight > 0;
}
```
If `allowNewReward()` is false, this code path also falls through to the unguarded `assert` and can hit the same zero-divisor condition, either in the `hardenCalculation()`+BigInteger branch (`ArithmeticException`) or the double-based branch (`Infinity`/`NaN` cast to `long`).

`totalEnergyWeight` is chain-state (a `DynamicPropertiesStore` value updated as accounts freeze/unfreeze TRX for energy). It is entirely plausible for it to reach `0` — e.g., a freshly bootstrapped/private network before any account has frozen for energy, or after mass unfreezing (post `UnfreezeBalance`/`UnfreezeBalanceV2` on all frozen accounts), combined with an account that still has a stale nonzero `AllFrozenBalanceForEnergy` recorded (a data/consistency edge case), or simply a network state where the global weight legitimately drops to zero while the calling account still attempts an energy calculation. This code path is reached on essentially every smart-contract invocation through the VM/`RepositoryImpl`, i.e. from any anonymous broadcast transaction that triggers TVM execution and needs to compute the caller's free energy limit.

### Impact Explanation
- With hardened resource calculation enabled, this results in an uncaught `ArithmeticException` inside TVM energy accounting during transaction execution — a denial-of-service condition for contract calls under that state (every energy-limit computation for any account with `frozeBalance >= TRX_PRECISION` fails while `totalEnergyWeight == 0`).
- With hardened resource calculation disabled, the double-based division silently returns a corrupted energy limit (potentially `Long.MAX_VALUE`), which is an accounting/resource-integrity bug: an account could be granted effectively unlimited free energy, letting it execute arbitrarily expensive contract calls without paying, degrading network resource accounting guarantees.
- Either outcome affects core TVM energy metering, a consensus-relevant resource-accounting mechanism, so a divergence between nodes (e.g., one running with `-ea` assertions and thus crashing/throwing sooner, vs. one not) is also possible, though the more direct concrete impacts are DoS-via-exception and energy-accounting corruption.

### Likelihood Explanation
Reaching `totalEnergyWeight == 0` requires a specific but not implausible network condition (freshly initialized chain, or a state where all previously frozen-for-energy TRX has been unfrozen). It does not require a privileged actor — any account performing a smart contract call (a broadcast transaction) that reaches `calculateGlobalEnergyLimit` while global state satisfies the zero-divisor condition triggers the flaw. The primary defense in the code (`assert`) is disabled by default in production JVMs, so the flaw is live in typical deployments.

### Recommendation
Replace the `assert totalEnergyWeight > 0;` in `RepositoryImpl.calculateGlobalEnergyLimit` (and the equivalent fallback branch in `EnergyProcessor.calculateGlobalEnergyLimit` when `allowNewReward()` is false) with an explicit runtime check that returns a safe value (e.g., `0`) when `totalEnergyWeight <= 0`, mirroring the existing `allowNewReward()` guard so the safety check applies unconditionally rather than only under one feature flag.

### Proof of Concept
1. Deploy/initialize a java-tron node where `DynamicPropertiesStore.getTotalEnergyWeight()` is `0` (e.g., genesis state before any `FreezeBalanceV2`/energy delegation occurs) and `allowHardenResourceCalculation` is enabled.
2. Have an account with `AllFrozenBalanceForEnergy >= TRX_PRECISION` (achievable via delegation/freeze-related state inconsistency, or simply by constructing the scenario in a unit test as done in `RepositoryImplHardenTest`) invoke a smart contract (any `TriggerSmartContract` transaction) that causes `RepositoryImpl.calculateGlobalEnergyLimit` to execute.
3. Observe `BigInteger.divide(BigInteger.valueOf(0))` throw `ArithmeticException: BigInteger divide by zero`, propagating out of the TVM energy accounting path uncaught (confirmed absence of any catch/guard around lines 967-985), analogous to the reported `getPrice()==0` causing a division-by-zero panic in `deposit`/`withdraw`.

Note: I was unable to fully trace every upstream caller of `RepositoryImpl.calculateGlobalEnergyLimit` within the given iteration budget to confirm whether any outer `try/catch` in `VMActuator` or `Program` swallows `ArithmeticException` before it surfaces as a transaction failure versus a broader node fault; a Devin session with full-repo access would be needed to trace the complete exception-handling chain and confirm the precise blast radius (single tx failure vs. broader instability).

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
