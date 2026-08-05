Based on my investigation, I found an analog in `EnergyProcessor.calculateGlobalEnergyLimit`, which is missing an explicit zero-check that its sibling method in `BandwidthProcessor` has.

### Title
Missing zero-weight guard in EnergyProcessor.calculateGlobalEnergyLimit relies on disabled Java assertion, allowing divide-by-zero panic - (File: chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java)

### Summary
`EnergyProcessor.calculateGlobalEnergyLimit` computes an account's energy limit by dividing by `totalEnergyWeight`. When the new-reward logic is inactive, the code falls back to a Java `assert` instead of a real runtime check to guard against `totalEnergyWeight <= 0`.

### Finding Description
`calculateGlobalEnergyLimit` reads `totalEnergyWeight` and only returns 0 early when `dynamicPropertiesStore.allowNewReward()` is true and the weight is non-positive; otherwise it falls into `else { assert totalEnergyWeight > 0; }` [1](#0-0) . Java `assert` statements are no-ops unless the JVM is started with `-ea`, which is not the default for production deployments, so this "guard" performs no check at runtime. If `totalEnergyWeight` is 0 in this branch, execution proceeds to `calculateGlobalLimitV1`, which does `BigInteger.valueOf(weight).multiply(...).divide(BigInteger.valueOf(totalWeight)).longValueExact()` — a straight `BigInteger` division by zero, throwing an unhandled `ArithmeticException` [2](#0-1) .

This is directly analogous to the reported Solidity bug class: a division operation lacking a zero check on a denominator that is expected — but not guaranteed — to be non-zero, causing an unhandled revert/exception.

Notably, the sibling method `BandwidthProcessor.calculateGlobalNetLimit` handles the same situation defensively with a real, unconditional runtime check: `if (totalNetWeight == 0) { return 0; }` in addition to the `allowNewReward()` branch [3](#0-2) . `EnergyProcessor` lacks this equivalent explicit check, relying only on the disabled `assert`.

`calculateGlobalEnergyLimit` is invoked from `useEnergy`, which is called during energy accounting whenever any unprivileged user triggers a smart contract (TVM execution path) [4](#0-3) .

### Impact Explanation
If `totalEnergyWeight` becomes zero while `allowNewReward()` is false and `hardenCalculation()` (`allowHardenResourceCalculation`) is enabled, any account with frozen balance for energy attempting to trigger a contract would hit an uncaught `ArithmeticException` inside `useEnergy`, which sits in the core transaction-processing path shared by every TVM call. I could not confirm within the given index whether this exception is caught generically further up the stack (e.g., in `VMActuator`/`TransactionTrace`) versus propagating and disrupting block/transaction processing — this needs verification with the full source, which the index does not fully expose for `TransactionTrace.java`.

### Likelihood Explanation
On established mainnet, `totalEnergyWeight` is very unlikely to be exactly zero at any point in chain history because energy has been frozen by numerous accounts since genesis. The realistic conditions for reachability are private/test networks or unusual future states where `allowNewReward` is disabled but hardened resource calculation is enabled and total frozen-for-energy weight drops to zero (all energy unfrozen). This narrows practical likelihood on public mainnet but the missing safety net compared to the analogous, correctly-guarded `BandwidthProcessor` code represents a genuine code-quality/robustness gap.

### Recommendation
Add an explicit, unconditional check in `EnergyProcessor.calculateGlobalEnergyLimit`, mirroring `BandwidthProcessor.calculateGlobalNetLimit`:
```java
if (totalEnergyWeight <= 0) {
  return 0;
}
```
before evaluating `hardenCalculation()`/`calculateGlobalLimitV1`, removing reliance on the `assert` statement (which is disabled in production).

### Proof of Concept
Not independently reproducible from the indexed context alone — a full reproduction would require constructing a private/test chain state where `allowNewReward` is disabled, `allowHardenResourceCalculation` is enabled, and `totalEnergyWeight` is driven to zero, then calling `useEnergy` (e.g., via triggering any smart contract) to observe the uncaught `ArithmeticException` from `calculateGlobalLimitV1`'s `BigInteger` division. I recommend a Devin session with full repo/test access to build and run this PoC and to trace whether the exception is caught elsewhere in the transaction-processing pipeline.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L102-120)
```java
  public boolean useEnergy(AccountCapsule accountCapsule, long energy, long now) {

    long energyUsage = accountCapsule.getEnergyUsage();
    long latestConsumeTime = accountCapsule.getAccountResource().getLatestConsumeTimeForEnergy();
    long energyLimit = calculateGlobalEnergyLimit(accountCapsule);
    long newEnergyUsage;
    if (!dynamicPropertiesStore.supportUnfreezeDelay()) {
      newEnergyUsage = increase(energyUsage, 0, latestConsumeTime, now);
    } else {
      // only participate in the calculation as a temporary variable, without disk flushing
      newEnergyUsage = recovery(accountCapsule, ENERGY, energyUsage,
          latestConsumeTime, now);
    }

    if (energy > (energyLimit - newEnergyUsage)
        && dynamicPropertiesStore.getAllowTvmFreeze() == 0
        && !dynamicPropertiesStore.supportUnfreezeDelay()) {
      return false;
    }
```

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

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L350-357)
```java
  protected long calculateGlobalLimitV1(long frozeBalance,
      long totalLimit, long totalWeight) {
    long weight = frozeBalance / TRX_PRECISION;
    return BigInteger.valueOf(weight)
        .multiply(BigInteger.valueOf(totalLimit))
        .divide(BigInteger.valueOf(totalWeight))
        .longValueExact();
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L440-447)
```java
    long totalNetLimit = chainBaseManager.getDynamicPropertiesStore().getTotalNetLimit();
    long totalNetWeight = chainBaseManager.getDynamicPropertiesStore().getTotalNetWeight();
    if (dynamicPropertiesStore.allowNewReward() && totalNetWeight <= 0) {
      return 0;
    }
    if (totalNetWeight == 0) {
      return 0;
    }
```
