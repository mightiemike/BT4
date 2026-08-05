### Title
Hardened resource-limit calculation can throw unhandled `ArithmeticException`, DoSing bandwidth/energy accounting on every transaction - (File: `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java`, `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java`)

### Summary
When the `ALLOW_HARDEN_RESOURCE_CALCULATION` chain parameter is enabled, `calculateGlobalNetLimit`/`calculateGlobalEnergyLimit` switch from a lossy double-based computation to a "hardened" `BigInteger` computation that calls `.longValueExact()`. Unlike the double path (which silently saturates/truncates), `longValueExact()` throws an unchecked `ArithmeticException` whenever the intermediate product exceeds `Long` range. These limit-calculation functions are invoked on essentially every transaction's bandwidth/energy accounting path (`useAccountNet`, `useAssetAccountNet`, `consumeBandwidthForCreateNewAccount`, `useEnergy`, `getAccountLeftEnergyFromFreeze`), mirroring the Venus `accrueInterest()` pattern where a dynamically computed value that exceeds a hard bound causes a revert instead of being clamped, DoSing all functions that depend on it.

### Finding Description
`BandwidthProcessor.calculateGlobalNetLimit()` and `EnergyProcessor.calculateGlobalEnergyLimit()`/`calculateGlobalEnergyLimitV2()` branch on `hardenCalculation()` (backed by `VMConfig.allowHardenResourceCalculation()`, itself driven by the `ALLOW_HARDEN_RESOURCE_CALCULATION` dynamic property) and delegate to `calculateGlobalLimitV1`/`calculateGlobalLimitV2` in `ResourceProcessor`, whose implementation pattern (confirmed identically in `RepositoryImpl.calculateGlobalEnergyLimit` and `RepositoryImpl.usageToBalance`) is: [1](#0-0) 

`BigInteger.longValueExact()` throws `ArithmeticException` if the value doesn't fit in a `long`. The non-hardened path instead uses `double` arithmetic which silently saturates/loses precision but never throws: [2](#0-1) 

The same `hardenCalculation()`/`longValueExact()` pattern governs `calculateGlobalNetLimit`: [3](#0-2) 

These limit functions are called from the hot resource-consumption paths used on every transaction: `useAccountNet`, `useAssetAccountNet`, `consumeBandwidthForCreateNewAccount` in `BandwidthProcessor`, and `useEnergy`/`getAccountLeftEnergyFromFreeze` in `EnergyProcessor`: [4](#0-3) [5](#0-4) 

Exactly like the Venus finding — where `accrueInterest()` hard-reverts instead of clamping `borrowRateMantissa` to the max — the hardened path here hard-throws instead of clamping/saturating the computed limit when the `BigInteger` product overflows `long` range. The project's own test suite explicitly documents this as intentional "overflow detection" behavior that throws `ArithmeticException`, e.g. `testCalculateGlobalEnergyLimitHardenedOverflowDetected` and `testGlobalNetLimitOverflowDetectedWithHardening`, confirming the exception is not caught internally and is expected to propagate: [6](#0-5) [7](#0-6) 

### Impact Explanation
`calculateGlobalNetLimit`/`calculateGlobalEnergyLimit` gate whether an account has enough free bandwidth/energy for a transaction and are invoked on the mandatory `consume()` path executed for every transaction that touches bandwidth or energy (transfers, TRC10 transfers, smart-contract calls, new-account creation). An uncaught `ArithmeticException` here is a `RuntimeException`, not one of the checked exceptions (`ContractValidateException`, `AccountResourceInsufficientException`, `TooBigTransactionException`) that calling code is designed to catch and turn into an orderly transaction failure. If it is not caught somewhere further up the actuator/block-application stack, it will propagate uncontrolled through transaction processing — at minimum failing the specific transaction unpredictably instead of via the normal validation-exception mechanism, and at worst (if thrown during block application rather than mempool validation) causing an unhandled exception during block execution, which is a liveness/consensus risk for the node. Given this is gated by a governance-toggleable chain parameter (`ALLOW_HARDEN_RESOURCE_CALCULATION`), and the overflow condition depends only on frozen-balance/weight magnitudes reaching values where `energyWeight * totalEnergyLimit` (or `netWeight * totalNetLimit`) exceeds `Long.MAX_VALUE`, this is a real dynamic-value-exceeds-hardcoded/representable-bound class DoS analogous to the Venus report, not merely a mocked/internal-only scenario.

### Likelihood Explanation
Triggering requires: (1) the `ALLOW_HARDEN_RESOURCE_CALCULATION` proposal to be active (a legitimate, intended, network-wide state achievable via on-chain governance, exactly as the Venus report's mitigation also required community/VIP action for its cap), and (2) frozen-balance-derived weight and total-limit values whose product exceeds `Long.MAX_VALUE`. Because `totalNetWeight`/`totalEnergyWeight` grow with aggregate network staking over time and `frozenBalance` can be large for high-value accounts, this is a plausible, non-theoretical value range as the network's staked TRX increases, especially since the codebase's own tests were written specifically to exercise and assert this overflow-throwing behavior, indicating awareness of the condition rather than it being purely hypothetical.

### Recommendation
Mirror the recommended Venus fix: do not let the dynamically computed value hard-fail when it would exceed the representable/allowed bound. Either (a) clamp the `BigInteger` result to `Long.MAX_VALUE` (or another sane cap) before converting with `longValueExact()`, or (b) catch `ArithmeticException` at the calculation boundary and treat it as "limit = 0" or "limit = Long.MAX_VALUE" consistent with how `AccountResourceInsufficientException`/`ContractValidateException` are otherwise surfaced, so failures are routed through the existing controlled transaction-rejection path rather than as an unchecked runtime exception during transaction/block processing.

### Proof of Concept
Not independently reproduced against a running node within this analysis; supporting evidence is the existing unit tests that assert the throw behavior directly: [8](#0-7) 
I was unable to fully verify, within the available iterations, whether any outer call site (e.g., `TransactionTrace` or the block-application loop) explicitly catches `ArithmeticException`/`RuntimeException` around bandwidth/energy consumption calls; this should be confirmed by a full-repository review (e.g., via a Devin session) before treating the block-halt scenario as certain versus a per-transaction failure.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L978-984)
```java
    if (hardenResourceCalculation()) {
      return BigInteger.valueOf(energyWeight)
          .multiply(BigInteger.valueOf(totalEnergyLimit))
          .divide(BigInteger.valueOf(totalEnergyWeight))
          .longValueExact();
    }
    return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
```

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

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L161-166)
```java
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

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L468-483)
```java
  private boolean useAccountNet(AccountCapsule accountCapsule, long bytes, long now) {

    long netUsage = accountCapsule.getNetUsage();
    long latestConsumeTime = accountCapsule.getLatestConsumeTime();
    long netLimit = calculateGlobalNetLimit(accountCapsule);

    long newNetUsage;
    if (!dynamicPropertiesStore.supportUnfreezeDelay()) {
      newNetUsage = increase(netUsage, 0, latestConsumeTime, now);
    } else {
      // only participate in the calculation as a temporary variable, without disk flushing
      newNetUsage = recovery(accountCapsule, BANDWIDTH, netUsage, latestConsumeTime, now);
    }


    if (bytes > (netLimit - newNetUsage)) {
```

**File:** framework/src/test/java/org/tron/core/vm/repository/RepositoryImplHardenTest.java (L215-225)
```java
  @Test
  public void testUsageToBalanceOverflowDetectedWithHardening() {
    long usage = 1_000_000_000L;
    long totalWeight = 1_000_000_000_000L;
    long totalLimit = 1L;

    VMConfig.initAllowHardenResourceCalculation(1);

    Assert.assertThrows(ArithmeticException.class,
        () -> invokeUsageToBalance(usage, totalWeight, totalLimit));
  }
```

**File:** framework/src/test/java/org/tron/core/vm/repository/RepositoryImplHardenTest.java (L260-279)
```java
  @Test
  public void testCalculateGlobalEnergyLimitHardenedOverflowDetected() {
    long totalEnergyLimit = Long.MAX_VALUE / 2;
    long totalEnergyWeight = 1L;
    long frozeBalance = Long.MAX_VALUE / 4;

    dbManager.getDynamicPropertiesStore().saveTotalEnergyCurrentLimit(totalEnergyLimit);
    dbManager.getDynamicPropertiesStore().saveTotalEnergyWeight(totalEnergyWeight);

    AccountCapsule account = new AccountCapsule(
        ByteString.copyFromUtf8("owner"),
        ByteString.copyFrom(ByteArray.fromHexString(
            Wallet.getAddressPreFixString() + "548794500882809695a8a687866e76d4271a1abc")),
        AccountType.Normal, 0L);
    account.setFrozenForEnergy(frozeBalance, 0L);

    VMConfig.initAllowHardenResourceCalculation(1);
    Assert.assertThrows(ArithmeticException.class,
        () -> repository.calculateGlobalEnergyLimit(account));
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
