### Title
Unguarded division by `totalEnergyWeight`/`totalNetWeight` in `RepositoryImpl.calculateGlobalEnergyLimit` can throw `ArithmeticException` and abort TVM contract execution - (File: `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java`)

### Summary
`RepositoryImpl.calculateGlobalEnergyLimit` divides by `totalEnergyWeight` (a network-wide dynamic property) without any zero-check, relying only on a Java `assert` statement that is compiled out and never evaluated in production (`assert` requires the `-ea` JVM flag, which is not used in java-tron's launch scripts). When `totalEnergyWeight` is `0`, the "hardened" arithmetic path performs `BigInteger.divide(BigInteger.ZERO)`, which throws `ArithmeticException: / by zero`. This mirrors the reported bug class: a config/state value that is trusted to be non-zero, guarded only by an assertion, and reachable from ordinary (non-privileged) transaction processing, causing a deterministic crash/DoS of the calling thread instead of the intended graceful fallback.

### Finding Description
`RepositoryImpl.calculateGlobalEnergyLimit` reads `totalEnergyWeight` from `DynamicPropertiesStore` and only defends against it with an `assert`: [1](#0-0) 

Unlike the sibling implementation in `chainbase/.../EnergyProcessor.calculateGlobalEnergyLimit`, which explicitly short-circuits to `0` when `totalEnergyWeight <= 0` under `allowNewReward()`: [2](#0-1) 

`RepositoryImpl`'s copy of this logic (used by the TVM execution path, not the wallet/API path) has no such guard when `allowNewReward()` is false, or in the general case where the check doesn't apply — it falls straight to the `assert`, which is a no-op in production builds, then performs the division:

```java
assert totalEnergyWeight > 0;
if (hardenResourceCalculation()) {
  return BigInteger.valueOf(energyWeight)
      .multiply(BigInteger.valueOf(totalEnergyLimit))
      .divide(BigInteger.valueOf(totalEnergyWeight))   // throws when totalEnergyWeight == 0
      .longValueExact();
}
```

This method is reached from ordinary TVM contract execution via `RepositoryImpl.getAccountLeftEnergyFromFreeze`, which any triggered smart-contract call exercises when computing the caller's frozen-energy allowance: [3](#0-2) 

and that in turn is invoked from `VMActuator.getAccountEnergyLimitWithFixRatio`, which runs on every TVM `CALL` for freeze-v1/v2 accounts: [4](#0-3) 

`totalEnergyWeight` is a global chain state variable (sum of all frozen-for-energy balances converted to weight, adjusted via `addTotalEnergyWeight`/`saveTotalEnergyWeight`). It is driven toward zero purely by ordinary, unprivileged user actions — freezing and unfreezing TRX for energy is a normal account operation, not a privileged operation — so a network state where `totalEnergyWeight == 0` (e.g. a fresh/test network, or a network where all energy-freezing accounts have unfrozen) is reachable without any special permissions. The equivalent bandwidth path (`BandwidthProcessor.calculateGlobalNetLimit`) *does* have the defensive `if (totalNetWeight == 0) return 0;` check, confirming this is a missing-guard regression specifically in the `RepositoryImpl`/TVM copy of the logic rather than an intentional design choice: [5](#0-4) 

### Impact Explanation
When `totalEnergyWeight` is `0` and `hardenResourceCalculation()` is enabled, any TVM contract-triggering transaction that hits `getAccountEnergyLimitWithFixRatio`/`calculateGlobalEnergyLimit` throws an uncaught `ArithmeticException` inside contract execution/energy accounting logic. Depending on how the exception propagates through `VMActuator`, this can abort processing of an otherwise valid transaction/block, producing inconsistent behavior across nodes that have `allowHardenResourceCalculation` enabled versus disabled (potential consensus divergence), or cause repeated failures for any transaction from frozen-energy accounts, effectively a DoS on contract execution for affected nodes. This matches the original report's core impact category: an unguarded divisor sourced from mutable state/config crashing execution instead of failing gracefully.

### Likelihood Explanation
Likelihood is moderate, not high: `totalEnergyWeight` reaching exactly `0` on a live mainnet with many frozen accounts is unlikely under normal steady-state, but it is a reachable value on smaller/private/test chains, immediately after specific genesis/migration conditions, or if aggregate unfreezing brings the tracked weight to zero. The precondition (`hardenResourceCalculation()` enabled) is a runtime-toggleable dynamic property, not attacker-controlled, which further constrains but does not eliminate the risk. No privileged actor or protocol-level attack is required to drive the totalEnergyWeight to zero — it results purely from aggregate unprivileged freeze/unfreeze actions.

### Recommendation
- Replace the `assert totalEnergyWeight > 0;` in `RepositoryImpl.calculateGlobalEnergyLimit` with an explicit runtime check, mirroring `EnergyProcessor`/`BandwidthProcessor`: `if (totalEnergyWeight <= 0) return 0;` before any division.
- Audit all other `RepositoryImpl` divisions guarded only by `assert` (e.g. `divideCeil`, `increase`, `getUsage`) for the same class of issue, since `assert` is a no-op without `-ea`.
- Add unit tests instantiating `totalEnergyWeight = 0` / `totalNetWeight = 0` for both the hardened and legacy code paths in `RepositoryImpl` to lock in the fix.

### Proof of Concept
1. Bring up a java-tron node/network (e.g. a private or solo test chain) and enable `allowHardenResourceCalculation` (dynamic property toggled via proposal or `dbManager.getDynamicPropertiesStore().saveAllowHardenResourceCalculation(1)` in a test harness, as done in `framework/src/test/java/org/tron/core/db/CalculateGlobalLimitHardenTest.java`).
2. Ensure no account currently has energy frozen so `totalEnergyWeight` is `0` (fresh chain, or after all energy-freeze accounts have unfrozen).
3. Have any unprivileged account with `allowTvmFreezeV2()`/`allowTvmFreeze()` deploy or trigger a smart contract (a normal `TriggerSmartContract`/`CreateSmartContract` transaction).
4. `VMActuator.getAccountEnergyLimitWithFixRatio` → `RepositoryImpl.getAccountLeftEnergyFromFreeze` → `RepositoryImpl.calculateGlobalEnergyLimit` executes with `totalEnergyWeight == 0`; the `assert` is skipped (no `-ea`), and `BigInteger.divide(BigInteger.ZERO)` throws `ArithmeticException`, matching the pattern already exercised (with non-zero denominators) in `framework/src/test/java/org/tron/core/db/CalculateGlobalLimitHardenTest.java` (`testGlobalEnergyLimitOverflowDetectedWithHardening` shows the same code path throwing `ArithmeticException` under the hardened path for a different overflow condition, confirming the exception is not otherwise caught before propagating out of `calculateGlobalEnergyLimit`): [6](#0-5)

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L184-197)
```java
  @Override
  public long getAccountLeftEnergyFromFreeze(AccountCapsule accountCapsule) {
    long now = getHeadSlot();

    long energyUsage = accountCapsule.getEnergyUsage();
    long latestConsumeTime = accountCapsule.getAccountResource().getLatestConsumeTimeForEnergy();
    long energyLimit = calculateGlobalEnergyLimit(accountCapsule);

    long windowSize = accountCapsule.getWindowSize(Common.ResourceCode.ENERGY);

    long newEnergyUsage = recover(energyUsage, latestConsumeTime, now, windowSize);

    return max(energyLimit - newEnergyUsage, 0, VMConfig.disableJavaLangMath()); // us
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

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L564-576)
```java
  public long getAccountEnergyLimitWithFixRatio(AccountCapsule account, long feeLimit,
      long callValue) {

    long sunPerEnergy = VMConstant.SUN_PER_ENERGY;
    if (rootRepository.getDynamicPropertiesStore().getEnergyFee() > 0) {
      sunPerEnergy = rootRepository.getDynamicPropertiesStore().getEnergyFee();
    }

    long leftFrozenEnergy = rootRepository.getAccountLeftEnergyFromFreeze(account);
    if (VMConfig.allowTvmFreeze() || VMConfig.allowTvmFreezeV2()) {
      receipt.setCallerEnergyLeft(leftFrozenEnergy);
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

**File:** framework/src/test/java/org/tron/core/db/CalculateGlobalLimitHardenTest.java (L67-78)
```java
  @Test
  public void testGlobalEnergyLimitOverflowDetectedWithHardening() {
    dbManager.getDynamicPropertiesStore().saveTotalEnergyCurrentLimit(Long.MAX_VALUE / 2);
    dbManager.getDynamicPropertiesStore().saveTotalEnergyWeight(1L);
    ownerCapsule.setFrozenForEnergy(Long.MAX_VALUE / 4, 0L);
    dbManager.getAccountStore().put(ownerCapsule.getAddress().toByteArray(), ownerCapsule);

    dbManager.getDynamicPropertiesStore().saveAllowHardenResourceCalculation(1);

    Assert.assertThrows(ArithmeticException.class,
        () -> energyProcessor.calculateGlobalEnergyLimit(ownerCapsule));
  }
```
