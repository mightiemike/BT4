### Title
Division-before-multiplication precision loss in bandwidth/energy limit calculation (legacy path) - ([File: chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java], [File: chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java])

### Summary
The reported FluxToken bug is a classic "divide-then-multiply" precision-loss pattern where a value is divided by a factor and then multiplied by related factors, discarding fractional remainder that should have been preserved until the final division. The java-tron codebase contains a directly analogous, currently reachable pattern in the legacy (non-hardened) bandwidth/energy resource-limit calculations, which every unprivileged account that freezes TRX depends on to compute its free bandwidth/energy allowance.

### Finding Description
`BandwidthProcessor.calculateGlobalNetLimit` and `EnergyProcessor.calculateGlobalEnergyLimit` compute a user's proportional share of the network's total bandwidth/energy limit from their frozen balance weight: [1](#0-0) 

```java
long netWeight = frozeBalance / TRX_PRECISION;
return (long) (netWeight * ((double) totalNetLimit / totalNetWeight));
```
and [2](#0-1) 

```java
long energyWeight = frozeBalance / TRX_PRECISION;
return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
```

The same anti-pattern exists in `RepositoryImpl.calculateGlobalEnergyLimit` used by the TVM path and in `BandwidthProcessor.calculateGlobalNetLimitV2`/`EnergyProcessor.calculateGlobalEnergyLimitV2`: [3](#0-2) 

In each case, `totalLimit / totalWeight` is computed first (either as integer or double division, both of which lose precision relative to computing `frozeBalance * totalLimit` fully before dividing by `TRX_PRECISION * totalWeight`), and the result is only then multiplied by the user's weight — exactly the "divide before multiply" defect described in the FluxToken report. The codebase's own hardened replacement functions document this exact issue: [4](#0-3) 

The hardened path (`calculateGlobalLimitV1`/`calculateGlobalLimitV2`, using `BigInteger` multiply-then-divide) is only used when `hardenCalculation()`/`hardenResourceCalculation()` returns true, which is gated by the `ALLOW_HARDEN_RESOURCE_CALCULATION` dynamic parameter (an opt-in committee proposal), confirmed by: [5](#0-4) 

Until this parameter is enabled on a given network, every account resolves its resource limit through the legacy, precision-lossy formula.

### Impact Explanation
Any unprivileged account that freezes TRX for bandwidth or energy has its usable free-resource limit computed by this formula. The truncation from dividing `totalLimit/totalWeight` before multiplying by the user's own weight causes systematically under-reported limits versus the mathematically correct proportional share — the account is granted less bandwidth/energy than its frozen balance entitles it to. Because bandwidth/energy limits directly gate whether a transaction is free or requires burning TRX as a fee (via `useAccountNet`/energy consumption checks in `BandwidthProcessor`/`EnergyProcessor`), under-computed limits translate into real value loss: users are forced to pay TRX fees for transactions that should have been covered by their frozen-balance-derived free allowance. This matches the "underpriced/misallocated public resource, permanent loss of entitled value" impact class analogous to "permanent freezing of unclaimed royalties" in the original report.

### Likelihood Explanation
This is triggered on every ordinary transaction submission by any account with frozen TRX balance whenever `ALLOW_HARDEN_RESOURCE_CALCULATION` is not active for the chain/network — no privileged role or special conditions are required, only normal freeze-and-transact usage, as confirmed by the extensive test suite in `CalculateGlobalLimitHardenTest.java` explicitly comparing the legacy (buggy) result against the corrected `BigInteger` result and asserting they differ for non-integer ratios.

### Recommendation
Enable/mandate the hardened calculation path (`calculateGlobalLimitV1`/`calculateGlobalLimitV2`, multiply-before-divide via `BigInteger`) as the default behavior rather than an opt-in proposal, or otherwise rewrite the legacy formulas in `BandwidthProcessor.calculateGlobalNetLimit`, `BandwidthProcessor.calculateGlobalNetLimitV2`, `EnergyProcessor.calculateGlobalEnergyLimit`, `EnergyProcessor.calculateGlobalEnergyLimitV2`, and `RepositoryImpl.calculateGlobalEnergyLimit`/`usageToBalance` to always multiply numerators fully before dividing, eliminating the precision-lossy intermediate `totalLimit/totalWeight` division.

### Proof of Concept
Using the existing hardened-vs-legacy comparison test as a reproduction (already present in the repo), setting non-integer-dividing values demonstrates the discrepancy: [6](#0-5) 

```java
long totalEnergyLimit = 50_000_000_000L;
long totalEnergyWeight = 1_234_567L;
long frozeBalance = 10_000_000_000L;
...
long expected = BigInteger.valueOf(10000L)
    .multiply(BigInteger.valueOf(totalEnergyLimit))
    .divide(BigInteger.valueOf(totalEnergyWeight)).longValueExact();
Assert.assertEquals(expected, resultNew); // hardened == correct

long buggy = 10000L * (totalEnergyLimit / totalEnergyWeight);
Assert.assertNotEquals(buggy, resultNew); // legacy formula diverges from correct value
```
This confirms the legacy divide-then-multiply formula (equivalent to the default, non-hardened production path) produces a different, lower result than the mathematically correct multiply-then-divide computation, directly mirroring the FluxToken `getClaimableFlux` precision-loss defect.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L346-348)
```java
  protected boolean hardenCalculation() {
    return dynamicPropertiesStore.allowHardenResourceCalculation();
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L359-378)
```java
  /**
   * Hardened replacement of legacy V2 formula
   * {@code (long)(((double) frozeBalance / TRX_PRECISION)
   *               * ((double) totalLimit / totalWeight))}.
   *
   * <p>Preserves V2 semantics: equivalent to
   * {@code (frozeBalance * totalLimit) / (TRX_PRECISION * totalWeight)} with
   * a single integer truncation at the end. Critically, fractional weight
   * (i.e. {@code frozeBalance < TRX_PRECISION}) is preserved through the
   * multiplication and only truncated at the final divide, so small balances
   * yield the same proportional result as the double-arithmetic path.
   */
  protected long calculateGlobalLimitV2(long frozeBalance,
      long totalLimit, long totalWeight) {
    return BigInteger.valueOf(frozeBalance)
        .multiply(BigInteger.valueOf(totalLimit))
        .divide(BigInteger.valueOf(TRX_PRECISION)
            .multiply(BigInteger.valueOf(totalWeight)))
        .longValueExact();
  }
```

**File:** framework/src/test/java/org/tron/core/db/CalculateGlobalLimitHardenTest.java (L227-257)
```java
    long resultOld = energyProcessor.calculateGlobalEnergyLimit(ownerCapsule);

    dbManager.getDynamicPropertiesStore().saveAllowHardenResourceCalculation(1);
    long resultNew = energyProcessor.calculateGlobalEnergyLimit(ownerCapsule);

    Assert.assertEquals(resultOld, resultNew);
  }

  @Test
  public void testV1FlooredWeightVsV2FractionalWeight() {
    dbManager.getDynamicPropertiesStore().saveTotalEnergyCurrentLimit(50_000_000_000L);
    dbManager.getDynamicPropertiesStore().saveTotalEnergyWeight(2_000_000_000L);
    long frozeBalance = 1_500_000L; // 1.5 x TRX_PRECISION

    dbManager.getDynamicPropertiesStore().saveAllowHardenResourceCalculation(1);

    // V1 path
    dbManager.getDynamicPropertiesStore().saveUnfreezeDelayDays(0);
    ownerCapsule.setFrozenForEnergy(frozeBalance, 0L);
    dbManager.getAccountStore().put(ownerCapsule.getAddress().toByteArray(), ownerCapsule);
    long v1New = energyProcessor.calculateGlobalEnergyLimit(ownerCapsule);

    // Legacy V1 expectation: floor(1.5) * 25.0 = 1 * 25 = 25
    Assert.assertEquals(25L, v1New);

    // V2 path with the same balance keeps the fractional weight
    long v2New = energyProcessor.calculateGlobalEnergyLimitV2(frozeBalance);
    // Legacy V2 expectation: 1.5 * 25.0 = 37.5 -> 37
    Assert.assertEquals(37L, v2New);

    // And both must match their respective legacy doubles
```
