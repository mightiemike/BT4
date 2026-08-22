### Title
Unguarded `assert totalEnergyWeight > 0` before BigInteger division in TVM energy-limit calculation causes DoS via unhandled `ArithmeticException` - (File: actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java)

### Summary
`RepositoryImpl.calculateGlobalEnergyLimit(AccountCapsule)` guards a division by `totalEnergyWeight` only with a Java `assert` statement, which is a no-op unless the JVM is started with `-ea`. If `totalEnergyWeight` (a chain-wide accumulator, not validated to be non-zero at this call site) is `0`, the subsequent `BigInteger.valueOf(...).divide(BigInteger.valueOf(totalEnergyWeight))` throws an unhandled `ArithmeticException`, propagating out of TVM contract execution triggered by an ordinary `TriggerSmartContract` transaction.

### Finding Description
`calculateGlobalEnergyLimit` computes the amount of energy an account can draw from its frozen-for-energy balance: [1](#0-0) 

The function reads `totalEnergyWeight` directly from `DynamicPropertiesStore.getTotalEnergyWeight()` and only "checks" it is positive with `assert totalEnergyWeight > 0;` before performing a `BigInteger` division by it (in the hardened-calculation branch) or a double division (in the legacy branch). Assertions are disabled by default in production JVMs (the `-ea` flag is not part of the standard node startup scripts), so this check is compiled out at runtime and provides no actual protection. If `totalEnergyWeight == 0`, the hardened path throws `ArithmeticException: / by zero` from `BigInteger.divide`, and the legacy double-division path silently produces `NaN`/`Infinity`, both of which are semantically broken states.

This differs from the sibling implementation in `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java`, which explicitly short-circuits with a real runtime check: [2](#0-1) 
Here, `if (dynamicPropertiesStore.allowNewReward() && totalEnergyWeight <= 0) { return 0; }` provides an actual guard — but only when `allowNewReward()` is enabled; the `else` branch still falls back to the same non-functional `assert totalEnergyWeight > 0;`.

The `RepositoryImpl` version (used inside TVM contract execution via `Repository`/`RepositoryImpl`, e.g. from `VMActuator.getAccountEnergyLimitWithFixRatio` → `rootRepository.getAccountLeftEnergyFromFreeze` → `calculateGlobalEnergyLimit`) has no equivalent `<= 0` guard at all — it relies solely on the disabled `assert`.

This mirrors the reported Gearbox bug class: a numeric configuration/state value (`totalEnergyWeight`, analogous to `maxLeverageFactor`) that can reach `0` is used as a divisor without an explicit runtime lower-bound check (`> 0`), with only a comment/assert standing in place of real validation.

### Impact Explanation
`totalEnergyWeight` is a globally shared counter, adjusted via `addTotalEnergyWeight`/`saveTotalEnergyWeight` whenever accounts freeze/unfreeze TRX for energy across the whole network: [3](#0-2) 
If this value were ever driven to `0` (e.g., through an edge case in unfreeze/undelegate accounting, migration, or a bug elsewhere that decrements it below the correctly-tracked amount), every subsequent smart-contract call that reaches `calculateGlobalEnergyLimit` for an account with frozen energy would throw an unhandled `ArithmeticException`, since production nodes run with assertions disabled. Because this is invoked from ordinary `TriggerSmartContract` execution (reachable from any anonymous broadcast transaction), it represents a node-wide denial-of-service risk on the TVM execution/energy-metering path rather than a privileged-actor issue.

### Likelihood Explanation
Likelihood is limited by how difficult it is to actually drive the global `totalEnergyWeight` counter to exactly `0` in a live network with active stakers — this requires either an accounting bug elsewhere in the freeze/unfreeze/undelegate lifecycle or a pathological state (all TRX ever frozen for energy simultaneously unfrozen), both of which are hard to prove reachable purely through the report/scan. The defensive-coding gap itself (relying on `assert` instead of a real guard) is concretely present and inconsistent with the parallel `EnergyProcessor` implementation that at least partially guards this case.

### Recommendation
Replace the `assert totalEnergyWeight > 0;` in `RepositoryImpl.calculateGlobalEnergyLimit` (and the equivalent fallback branch in `EnergyProcessor.calculateGlobalEnergyLimit`) with an explicit runtime check, e.g.:
```java
if (totalEnergyWeight <= 0) {
  return 0;
}
```
before performing either the `BigInteger` division or the double division, so a zero/negative divisor can never reach the division regardless of JVM assertion settings.

### Proof of Concept
Not independently reproducible from static analysis alone: triggering `totalEnergyWeight == 0` requires either (a) running the node with `-ea` disabled (the default) while an as-yet-unidentified accounting bug drives the shared counter to zero, or (b) directly unit-testing `RepositoryImpl.calculateGlobalEnergyLimit` with a `DynamicPropertiesStore` mocked to return `getTotalEnergyWeight() == 0`, which will throw `ArithmeticException` on the hardened path (confirmed by code inspection of `RepositoryImpl.java:967-985`). No test in the repository currently exercises this exact zero-weight case for `RepositoryImpl` (existing `CalculateGlobalLimitHardenTest`/`RepositoryImplHardenTest` cases use nonzero weights), so full exploitability of the zero-weight precondition itself is unconfirmed.

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

**File:** actuator/src/main/java/org/tron/core/vm/repository/Repository.java (L148-156)
```java
  void addTotalNetWeight(long amount);

  void addTotalEnergyWeight(long amount);

  void addTotalTronPowerWeight(long amount);

  void saveTotalNetWeight(long totalNetWeight);

  void saveTotalEnergyWeight(long totalEnergyWeight);
```
