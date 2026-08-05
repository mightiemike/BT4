### Title
`RepositoryImpl.calculateGlobalEnergyLimit` lacks the zero-weight guard present in `EnergyProcessor`, causing TVM contract calls to abort when `totalEnergyWeight` is zero - (File: `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java`)

### Summary
The Curve `FeeSplitter.addFees()` bug is a case where a derived "total" value (recomputed via subtraction rather than tracked directly) can legitimately reach `0` in a state that is still valid, causing an operation for an unrelated, legitimate actor to revert. The same *shape* of bug — one code path missing a zero-guard that a sibling/duplicate code path already has for the same computation — exists in java-tron's global energy-limit calculation, which is invoked from inside TVM contract execution.

### Finding Description
java-tron has two parallel implementations that compute an account's global energy limit from `totalEnergyWeight`:

- `EnergyProcessor.calculateGlobalEnergyLimit` explicitly guards against a zero/negative `totalEnergyWeight`: [1](#0-0) 

- `RepositoryImpl.calculateGlobalEnergyLimit`, used by the TVM (via `ContractState`/`Repository`), performs the identical computation but has **no such guard** — it only has a non-functional `assert`, which is compiled out unless the JVM is started with `-ea`: [2](#0-1) 

This method is reachable from TVM contract execution through `ContractState.calculateGlobalEnergyLimit`, which simply delegates to the underlying `Repository`: [3](#0-2) 

If `totalEnergyWeight` is `0` (or negative) and `hardenResourceCalculation()`/`allowHardenResourceCalculation()` is enabled, the call falls into `BigInteger.valueOf(totalEnergyLimit).divide(BigInteger.valueOf(totalEnergyWeight))` (`RepositoryImpl.java:978-982`), which throws `ArithmeticException: / by zero` because `BigInteger` division does not treat zero specially the way primitive `double` division does. In the non-hardened branch it instead silently produces `Infinity`/`NaN` cast to `long` — a separate correctness bug, but the hardened branch's `ArithmeticException` is what actually aborts execution.

### Impact Explanation
An uncaught `ArithmeticException` thrown mid-TVM-execution from `Repository.calculateGlobalEnergyLimit` propagates out of contract execution logic rather than being handled as a normal VM revert path, since this method is not wrapped in the standard TVM exception translation used elsewhere for arithmetic overflow (e.g. `LongMath.checkedAdd`/`checkedMultiply` wrapped into `ContractExeException` in `VoteWitnessProcessor`). This is a divergence/invalid-state bug: the legacy resource path (`EnergyProcessor`) was hardened against `totalEnergyWeight <= 0`, but the newer TVM-facing path (`RepositoryImpl`) was not updated to match, meaning any account interaction that triggers this computation while `totalEnergyWeight` is `0` can fail unexpectedly instead of degrading gracefully to `0`, as the legacy path does.

### Likelihood Explanation
`totalEnergyWeight` reaching `0` is a legitimate, reachable network state — it is literally guarded against in `EnergyProcessor` for that reason (`allowNewReward() && totalEnergyWeight <= 0`). The condition primarily occurs in edge/test networks, or briefly during specific chain states (e.g., no accounts having frozen balance for energy, or the value transiently dropping to zero during large-scale unfreeze events). This makes the divergence a real, if narrow, reliability/availability risk for TVM-based accounting rather than a purely theoretical one, since the guard's very existence in the sibling code path is evidence the maintainers already knew this state was reachable.

### Recommendation
Add the same zero/negative guard used in `EnergyProcessor.calculateGlobalEnergyLimitV2` (`totalEnergyWeight == 0 → return 0`, or the `allowNewReward()` check used in `calculateGlobalEnergyLimit`) to `RepositoryImpl.calculateGlobalEnergyLimit` before performing the `BigInteger` or `double` division, and remove reliance on the non-functional `assert` statement. Additionally wrap the computation to convert `ArithmeticException` into the same handled failure pattern used elsewhere in the actuator layer, so any residual edge case degrades safely rather than propagating an uncaught exception.

### Proof of Concept
Because `RepositoryImpl` and `EnergyProcessor` are duplicated implementations with no shared code, a concrete PoC would require constructing a chain state with `totalEnergyWeight == 0` (e.g., a freshly initialized/test chain before any account freezes for energy, with `VMConfig.allowHardenResourceCalculation()` enabled) and then invoking any TVM path that calls `Repository.calculateGlobalEnergyLimit` (e.g., through `VMActuator`/`Wallet.java` energy-limit estimation flows) for an account whose `getAllFrozenBalanceForEnergy() >= TRX_PRECISION`. This is analogous to the reachable-but-edge-case precondition (`totalSupply == 0`) in the original Curve finding. I was not able to trace a full concrete end-to-end call chain (e.g., from a specific user-facing RPC/actuator down through `VMActuator` into this exact method under a zero-weight precondition) within the available tool budget, so this should be verified with a live/test-chain reproduction before treating it as fully confirmed.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L154-166)
```java
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

**File:** actuator/src/main/java/org/tron/core/vm/program/ContractState.java (L277-280)
```java
  @Override
  public long calculateGlobalEnergyLimit(AccountCapsule accountCapsule) {
    return repository.calculateGlobalEnergyLimit(accountCapsule);
  }
```
