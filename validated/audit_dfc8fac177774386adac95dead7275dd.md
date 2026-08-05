### Title
Unguarded `assert` allows division-by-zero on `totalEnergyWeight` in TVM energy-limit calculation - ([File: actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java])

### Summary
`RepositoryImpl.calculateGlobalEnergyLimit` computes an account's energy limit by dividing by `totalEnergyWeight`, guarding the zero case only with a Java `assert` statement rather than an explicit runtime check [1](#0-0) . This mirrors the Atlendis pattern where a validation invariant ("this value cannot be zero") is asserted/assumed in one place while the actual enforced check lives elsewhere (or nowhere) in the calling path, so a caller can reach the vulnerable code without the invariant being guaranteed.

### Finding Description
The chainbase-level `EnergyProcessor.calculateGlobalEnergyLimit` (used for normal transaction bandwidth/energy consumption) explicitly checks `totalEnergyWeight <= 0` and returns `0` before doing the division, only falling back to `assert totalEnergyWeight > 0` as a secondary internal sanity check [2](#0-1) . The TVM-facing `RepositoryImpl.calculateGlobalEnergyLimit`, however, has no such explicit `if (totalEnergyWeight <= 0) return 0;` guard — it relies solely on `assert totalEnergyWeight > 0;` before dividing [1](#0-0) .

Java assertions are disabled by default at runtime unless the JVM is started with `-ea`. In that (default) configuration the `assert` statement is a complete no-op, so the "validation" that the report's bug class targets doesn't actually execute in production — this is functionally identical to the Atlendis case where validation was assumed to have happened elsewhere but was not enforced at the point of use. If `totalEnergyWeight` is ever `0` (e.g., before any account freezes TRX for energy on a fresh/private chain, or after all energy-freeze weight is removed), `getUsage`/`calculateGlobalEnergyLimit` will perform an integer or `BigInteger` division by zero, throwing an unguarded `ArithmeticException`.

This `Repository` implementation is exposed to TVM smart-contract execution through `ContractState`, which simply forwards `calculateGlobalEnergyLimit` calls to the underlying `Repository` [3](#0-2) , meaning the vulnerable path is reachable from contract execution (e.g., calls into `VMActuator`/`ContractState`), not just from privileged system code.

### Impact Explanation
An unguarded `ArithmeticException` thrown mid-transaction-execution inside the TVM energy accounting path would propagate as an unexpected runtime exception rather than a handled `ContractValidateException`/`ContractExeException`. Depending on how the exception is caught up the call stack, this can cause inconsistent handling of a transaction (potential node divergence between nodes that hit the code path under different states, or a processing halt for that transaction/block), which is a state-divergence/halt class impact rather than a benign revert.

### Likelihood Explanation
Likelihood is constrained by how `totalEnergyWeight` reaches zero in a live, non-genesis mainnet — it is unlikely on a mature chain where energy-freeze weight is broadly distributed, but it is realistic on a freshly bootstrapped/private chain, or transiently after protocol changes to freezing logic (e.g., unfreeze delay/TVM freeze features altering weight accounting) before energy is frozen by any account. The core weakness — relying on a disabled-by-default `assert` instead of an explicit guard, in exactly the code path that lacks the protective check present in its sibling implementation — is a concrete, provable code defect matching the reported bug class.

### Recommendation
Add the same explicit `totalEnergyWeight <= 0` (or `== 0`) guard to `RepositoryImpl.calculateGlobalEnergyLimit` that already exists in `EnergyProcessor.calculateGlobalEnergyLimit`, returning `0` instead of relying on `assert`. More broadly, replace `assert`-based invariant checks in production code paths (which are silently disabled without `-ea`) with explicit conditional checks, and keep such validation colocated with every use site that performs the division, rather than assuming it was already enforced by a sibling/parallel implementation.

### Proof of Concept
1. Deploy/operate a node where `DynamicPropertiesStore.getTotalEnergyWeight()` returns `0` (e.g., a fresh chain/shadow fork before any `FreezeBalance`/`FreezeBalanceV2` for energy has occurred, or after all energy weight is removed).
2. Trigger any TVM execution path that calls `Repository.calculateGlobalEnergyLimit(accountCapsule)` through `ContractState`/`VMActuator` (e.g., a contract call that consults the account's frozen energy limit) while `frozeBalance >= TRX_PRECISION` for the account in question [4](#0-3) .
3. Because `totalEnergyWeight == 0` and the `assert` does not execute under default JVM settings, execution proceeds to divide by `totalEnergyWeight`, either via `BigInteger.divide(BigInteger.valueOf(0))` (hardened path) or `totalEnergyLimit/totalEnergyWeight` as a `double` (legacy path), producing an `ArithmeticException` or `NaN`-derived value [5](#0-4) .

Note: I could not fully verify from the indexed code the exact JVM launch flags used in the FullNode/production startup scripts (whether `-ea` is ever enabled), nor trace every call site that reaches `Repository.calculateGlobalEnergyLimit` from `VMActuator`/opcodes end-to-end within the indexing limits; a Devin session with full repository and build-script access would be needed to confirm the exact reachability and runtime assertion configuration with certainty.

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

**File:** actuator/src/main/java/org/tron/core/vm/program/ContractState.java (L277-280)
```java
  @Override
  public long calculateGlobalEnergyLimit(AccountCapsule accountCapsule) {
    return repository.calculateGlobalEnergyLimit(accountCapsule);
  }
```
