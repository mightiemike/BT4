## Title
Unguarded division by `totalEnergyWeight` in `RepositoryImpl.calculateGlobalEnergyLimit` relies on a no-op `assert`, enabling a division-by-zero DoS analogous to the SaiTub/SaiTop bug class - (File: actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java)

### Summary
The external report's bug class is: a privileged/administratively-set parameter can legitimately be (or become) `0`, and downstream code divides by it without a runtime guard, causing throws that block normal (unprivileged) user operations. The java-tron analog is `RepositoryImpl.calculateGlobalEnergyLimit`, which divides by `totalEnergyWeight` guarded only by a Java `assert` statement — a construct that is compiled out / disabled by default at runtime (JVMs run without `-ea` in production) [1](#0-0) .

### Finding Description
`calculateGlobalEnergyLimit` computes a user's TVM energy allowance from frozen-for-energy balance:

```
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
``` [1](#0-0) 

`totalEnergyWeight` is a chain-wide dynamic property that tracks the sum of all TRX frozen for energy across the network — it is not a value an individual unprivileged caller controls, but it is a value that can legitimately be `0` (e.g., a freshly bootstrapped chain/sidechain before anyone freezes for energy, or after all energy freezes are withdrawn under `UnfreezeBalanceV2`/`UnDelegateResource` flows). Unlike the equivalent implementations in `EnergyProcessor.calculateGlobalEnergyLimit`/`calculateGlobalEnergyLimitV2` and `BandwidthProcessor.calculateGlobalNetLimit`/`calculateGlobalNetLimitV2`, which explicitly check `if (totalEnergyWeight == 0) { return 0; }` before dividing [2](#0-1) [3](#0-2) , the `RepositoryImpl` (TVM-path) version relies solely on `assert totalEnergyWeight > 0;`. Java assertions are disabled by default (`-ea` must be explicitly passed to the JVM), so in a standard production deployment this line is a no-op and provides zero protection.

When `hardenResourceCalculation()` is enabled (an on-chain toggle, `allowHardenResourceCalculation`, exercised extensively in the hardened test suite such as `CalculateGlobalLimitHardenTest`) [4](#0-3) , the code path uses `BigInteger.divide`, which throws `ArithmeticException: BigInteger divide by zero` if `totalEnergyWeight == 0`. This function is reached from `VMActuator.getAccountEnergyLimitWithFixRatio`, invoked on the hot path of every `TriggerSmartContract`/`CreateSmartContract` transaction via `rootRepository.getAccountLeftEnergyFromFreeze(account)` and `rootRepository.calculateGlobalEnergyLimit(account)` [5](#0-4) [6](#0-5) .

### Impact Explanation
If `totalEnergyWeight` is `0` (a legitimate, reachable network state) and `hardenResourceCalculation` is active, any unprivileged user with a non-trivial `frozeBalance` for energy who submits a smart-contract transaction triggers an uncaught `ArithmeticException` deep in the TVM-invocation path used by every full node applying that transaction. Because this executes during transaction/block processing (not inside a narrow try/catch designed for arithmetic errors, unlike the `ExchangeCreateActuator`/`ExchangeInjectActuator` patterns that explicitly catch `ArithmeticException`), it risks failing block validation consistently across nodes, effectively halting contract execution for affected accounts network-wide until `totalEnergyWeight` recovers — directly matching the report's "system becomes temporarily or fully blocked" impact class.

### Likelihood Explanation
Reaching `totalEnergyWeight == 0` does not require malicious admin action (unlike the original Sai `cage(0)` scenario) — it can occur naturally (new/private chains prior to any energy freeze, or windows where all energy-frozen TRX is unfrozen). Whether `hardenResourceCalculation` is enabled by default in the deployed configuration and whether an upstream guard elsewhere prevents `totalEnergyWeight` from reaching `0` before this code runs could not be fully confirmed within the available search — this uncertainty should be validated against the live `ForkController`/proposal defaults and any additional call-site guards in `getAccountLeftEnergyFromFreeze` that were not fully traced due to iteration limits.

### Recommendation
Replace `assert totalEnergyWeight > 0;` in `RepositoryImpl.calculateGlobalEnergyLimit` with an explicit runtime guard consistent with the sibling implementations, e.g. `if (totalEnergyWeight <= 0) { return 0; }` before both the hardened `BigInteger` path and the legacy `double` path, matching the pattern already used in `EnergyProcessor.calculateGlobalEnergyLimitV2` and `BandwidthProcessor.calculateGlobalNetLimitV2`.

### Proof of Concept
1. Deploy/operate a node with `allowHardenResourceCalculation` enabled.
2. Reach or force a state where `DynamicPropertiesStore.getTotalEnergyWeight()` returns `0` (e.g., genesis before freezes, or after all `UnfreezeBalanceV2` operations remove frozen-for-energy TRX network-wide).
3. Have any account with `allFrozenBalanceForEnergy >= TRX_PRECISION` submit a `TriggerSmartContract` transaction.
4. `VMActuator.getAccountEnergyLimitWithFixRatio` → `RepositoryImpl.calculateGlobalEnergyLimit` executes `BigInteger.valueOf(totalEnergyLimit).divide(BigInteger.valueOf(0))`, throwing `ArithmeticException`, since the guarding `assert` is a no-op in production JVMs.

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

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L168-173)
```java
  public long calculateGlobalEnergyLimitV2(long frozeBalance) {
    long totalEnergyLimit = dynamicPropertiesStore.getTotalEnergyCurrentLimit();
    long totalEnergyWeight = dynamicPropertiesStore.getTotalEnergyWeight();
    if (totalEnergyWeight == 0) {
      return 0;
    }
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L455-460)
```java
  public long calculateGlobalNetLimitV2(long frozeBalance) {
    long totalNetLimit = dynamicPropertiesStore.getTotalNetLimit();
    long totalNetWeight = dynamicPropertiesStore.getTotalNetWeight();
    if (totalNetWeight == 0) {
      return 0;
    }
```

**File:** framework/src/test/java/org/tron/core/db/CalculateGlobalLimitHardenTest.java (L130-141)
```java
  @Test
  public void testGlobalNetLimitOverflowDetectedWithHardening() {
    dbManager.getDynamicPropertiesStore().saveTotalNetLimit(Long.MAX_VALUE / 2);
    dbManager.getDynamicPropertiesStore().saveTotalNetWeight(1L);
    ownerCapsule.setFrozenForBandwidth(Long.MAX_VALUE / 4, 0L);
    dbManager.getAccountStore().put(ownerCapsule.getAddress().toByteArray(), ownerCapsule);

    dbManager.getDynamicPropertiesStore().saveAllowHardenResourceCalculation(1);

    Assert.assertThrows(ArithmeticException.class,
        () -> bandwidthProcessor.calculateGlobalNetLimit(ownerCapsule));
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L564-602)
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

    long energyFromBalance = max(account.getBalance() - callValue, 0,
        VMConfig.disableJavaLangMath()) / sunPerEnergy;
    long availableEnergy = addExact(leftFrozenEnergy, energyFromBalance,
        VMConfig.disableJavaLangMath());

    long energyFromFeeLimit = feeLimit / sunPerEnergy;
    if (VMConfig.allowTvmFreezeV2()) {
      long now = rootRepository.getHeadSlot();
      EnergyProcessor energyProcessor =
          new EnergyProcessor(
              rootRepository.getDynamicPropertiesStore(),
              ChainBaseManager.getInstance().getAccountStore());
      energyProcessor.updateUsage(account);
      account.setLatestConsumeTimeForEnergy(now);
      receipt.setCallerEnergyUsage(account.getEnergyUsage());
      receipt.setCallerEnergyWindowSize(account.getWindowSize(ENERGY));
      receipt.setCallerEnergyWindowSizeV2(account.getWindowSizeV2(ENERGY));
      account.setEnergyUsage(
          energyProcessor.increase(account, ENERGY,
              account.getEnergyUsage(), min(leftFrozenEnergy, energyFromFeeLimit,
                  VMConfig.disableJavaLangMath()), now, now));
      receipt.setCallerEnergyMergedUsage(account.getEnergyUsage());
      receipt.setCallerEnergyMergedWindowSize(account.getWindowSize(ENERGY));
      rootRepository.updateAccount(account.createDbKey(), account);
    }
    return min(availableEnergy, energyFromFeeLimit, VMConfig.disableJavaLangMath());
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L626-630)
```java
      long totalEnergyFromFreeze = rootRepository
          .calculateGlobalEnergyLimit(account);
      long leftBalanceForEnergyFreeze = getEnergyFee(totalBalanceForEnergyFreeze,
          leftEnergyFromFreeze,
          totalEnergyFromFreeze);
```
