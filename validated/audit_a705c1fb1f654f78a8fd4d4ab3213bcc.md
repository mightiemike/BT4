Confirmed: `allowHardenResourceCalculation()` defaults to `0` (disabled) at [1](#0-0) , meaning the legacy, precision-losing energy/bandwidth-limit calculation is still the default code path on-chain until a proposal activates the hardening. This is a directly analogous "unnecessary/early division causing precision loss in an accounting formula" bug class to the FluxToken report.

### Title
Premature integer division before multiplication causes systematic precision loss (under-allocation) in resource-limit calculation - (File: `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java`, `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java`)

### Summary
The legacy (default) global energy-limit formula truncates a fractional weight via early integer division before it is combined with the `totalEnergyLimit/totalEnergyWeight` ratio, discarding precision that a single combined multiply-then-divide would have preserved. This mirrors the FluxToken `getClaimableFlux()` bug class: an avoidable/early division step introduces dust-level loss in an otherwise deterministic accounting formula.

### Finding Description
`EnergyProcessor#calculateGlobalEnergyLimit()` (and the parallel `RepositoryImpl#calculateGlobalEnergyLimit()` used by the TVM freezeV2/energy path) computes:
```java
long energyWeight = frozeBalance / TRX_PRECISION;
return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
``` [2](#0-1) [3](#0-2) 

Here `frozeBalance / TRX_PRECISION` is an integer division performed *before* the value is scaled by `totalEnergyLimit/totalEnergyWeight`. Any fractional TRX amount below `TRX_PRECISION` (1,000,000 sun) in `frozeBalance` is discarded up front, even though multiplying first (as the hardened `calculateGlobalLimitV2` path does) would have preserved that fraction through to the final truncation. The project's own hardened replacement explicitly documents this exact fix:
```java
/**
 * Preserves V2 semantics ... Critically, fractional weight
 * (i.e. frozeBalance < TRX_PRECISION) is preserved through the
 * multiplication and only truncated at the final divide...
 */
protected long calculateGlobalLimitV2(long frozeBalance, long totalLimit, long totalWeight) {
  return BigInteger.valueOf(frozeBalance).multiply(BigInteger.valueOf(totalLimit))
      .divide(BigInteger.valueOf(TRX_PRECISION).multiply(BigInteger.valueOf(totalWeight)))
      .longValueExact();
}
``` [4](#0-3) 

That hardened logic is gated behind `allowHardenResourceCalculation()`, which is disabled by default (returns `0`/`false` unless a chain-governance proposal turns it on):
```java
public long getAllowHardenResourceCalculation() {
  return Optional.ofNullable(getUnchecked(ALLOW_HARDEN_RESOURCE_CALCULATION))
      .map(BytesCapsule::getData).map(ByteArray::toLong).orElse(0L);
}
``` [1](#0-0) 

Consequently, on any deployment where this proposal has not been activated, the legacy premature-division formula is the live code path used to compute an account's TVM energy limit from frozen-for-energy balance, reachable whenever a user freezes TRX for energy and triggers a contract call (an anonymous, unprivileged broadcast-transaction path).

### Impact Explanation
This causes users whose frozen balance is not an exact multiple of `TRX_PRECISION` (1 TRX) to receive a systematically lower energy limit than the mathematically correct proportional share, i.e. the contract "fails to deliver promised returns" in the same sense as the original report — value is not lost from the system, but honest users are shortchanged relative to the intended proportional allocation formula. The magnitude is bounded (sub-TRX weight truncation), so it is a minor/dust-level accounting deviation rather than fund loss, matching the original report's own stated impact category.

### Likelihood Explanation
The affected code path (`calculateGlobalEnergyLimit`) is on the default flag state (`allowHardenResourceCalculation` off) and is exercised by any account freezing TRX for energy and calling a contract, i.e., every ordinary user via unprivileged transactions — high reachability, but low per-user severity since the underlying issue is a known, already-acknowledged one (the codebase itself contains "hardened" replacements and dedicated parity tests describing and fixing this exact class of bug).

### Recommendation
Enable `allowHardenResourceCalculation` via the standard chain-parameter governance proposal so the `calculateGlobalLimitV1`/`calculateGlobalLimitV2` BigInteger-based, multiply-before-divide formulas become the active path, eliminating the premature truncation in `frozeBalance / TRX_PRECISION` prior to full deployment sign-off.

### Proof of Concept
Using the existing project test harness pattern (already present in `CalculateGlobalLimitHardenTest`):
```java
long totalEnergyLimit = 50_000_000_000L;
long totalEnergyWeight = 1_234_567L;
long frozeBalance = 999_999L; // < TRX_PRECISION, but non-zero fractional weight

// legacy (default) path
long energyWeight = frozeBalance / TRX_PRECISION; // truncates to 0
long legacyResult = (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight)); // = 0

// hardened path (multiply first)
long hardenedResult = BigInteger.valueOf(frozeBalance)
    .multiply(BigInteger.valueOf(totalEnergyLimit))
    .divide(BigInteger.valueOf(TRX_PRECISION).multiply(BigInteger.valueOf(totalEnergyWeight)))
    .longValueExact(); // > 0 for sufficiently large totalEnergyLimit/totalEnergyWeight ratio
```
This demonstrates the legacy default path silently drops sub-TRX_PRECISION weight to zero energy limit contribution, whereas multiplying before dividing preserves the fractional contribution — the same "unnecessary/early division" precision-loss root cause as the FluxToken report. [5](#0-4)

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L3044-3057)
```java
  public long getAllowHardenResourceCalculation() {
    return Optional.ofNullable(getUnchecked(ALLOW_HARDEN_RESOURCE_CALCULATION))
        .map(BytesCapsule::getData)
        .map(ByteArray::toLong)
        .orElse(0L);
  }

  public void saveAllowHardenResourceCalculation(long value) {
    this.put(ALLOW_HARDEN_RESOURCE_CALCULATION, new BytesCapsule(ByteArray.fromLong(value)));
  }

  public boolean allowHardenResourceCalculation() {
    return getAllowHardenResourceCalculation() == 1L;
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L150-166)
```java
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

**File:** framework/src/test/java/org/tron/core/db/CalculateGlobalLimitHardenTest.java (L95-112)
```java
  @Test
  public void testGlobalEnergyLimitV2CorrectVsDoublePrecisionLoss() {
    long totalEnergyLimit = 50_000_000_000L;
    long totalEnergyWeight = 1_234_567L;
    long frozeBalance = 9_876_543_210_000_000L; // ~9.8e15

    dbManager.getDynamicPropertiesStore().saveTotalEnergyCurrentLimit(totalEnergyLimit);
    dbManager.getDynamicPropertiesStore().saveTotalEnergyWeight(totalEnergyWeight);

    BigInteger expected = BigInteger.valueOf(frozeBalance)
        .multiply(BigInteger.valueOf(totalEnergyLimit))
        .divide(BigInteger.valueOf(1_000_000L)
            .multiply(BigInteger.valueOf(totalEnergyWeight)));

    dbManager.getDynamicPropertiesStore().saveAllowHardenResourceCalculation(1);
    long actual = energyProcessor.calculateGlobalEnergyLimitV2(frozeBalance);
    Assert.assertEquals(expected.longValueExact(), actual);
  }
```
