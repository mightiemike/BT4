### Title
Integer division-before-multiplication truncates frozen-balance weight in energy/bandwidth limit calculation, causing systematic resource-entitlement loss - (File: `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java`, `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java`)

### Summary
`EnergyProcessor.calculateGlobalEnergyLimit()` (and its hardened counterpart `ResourceProcessor.calculateGlobalLimitV1()`) compute an account's proportional share of network-wide energy by first performing an integer division `frozeBalance / TRX_PRECISION` to derive a `weight`, and only *then* multiplying that truncated weight by the `totalEnergyLimit/totalEnergyWeight` ratio. This is the exact "hidden division before multiplication" antipattern described in the external report: truncating the numerator early loses the fractional part of `frozeBalance` before it is ever combined with `totalEnergyLimit`, producing a result that can be significantly smaller than the mathematically correct proportional share.

### Finding Description
In the legacy (pre-`supportUnfreezeDelay`) energy-limit code path: [1](#0-0) 

when `hardenCalculation()` is false, the code does:
```
long energyWeight = frozeBalance / TRX_PRECISION;
return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
```
and when `hardenCalculation()` is true, it calls `calculateGlobalLimitV1`: [2](#0-1) 

```
long weight = frozeBalance / TRX_PRECISION;
return BigInteger.valueOf(weight).multiply(BigInteger.valueOf(totalLimit))
    .divide(BigInteger.valueOf(totalWeight)).longValueExact();
```

In both variants, `frozeBalance / TRX_PRECISION` is computed as an **integer division first**, discarding any fractional TRX below 1,000,000 sun (`TRX_PRECISION`) before the value is ever multiplied by `totalLimit`. This mirrors precisely the Bonding.sol bug: a division is performed before a multiplication that could otherwise have preserved precision, causing the intermediate truncation to compound into the final result.

By contrast, the code's own comment on `calculateGlobalLimitV2` explicitly acknowledges this exact defect and describes the fix as "preserving fractional weight ... through the multiplication and only truncating at the final divide": [3](#0-2) 

This confirms the V1/legacy path (used whenever `dynamicPropertiesStore.supportUnfreezeDelay()` is false) still contains the unfixed precision-loss pattern, unlike V2 which correctly multiplies before dividing.

### Impact Explanation
`calculateGlobalEnergyLimit()` is invoked on every `useEnergy()` call and every `getAccountLeftEnergyFromFreeze()` call, i.e., on every smart-contract-invoking transaction submitted by any account: [4](#0-3) 

Any account that froze TRX for energy in an amount that is not an exact multiple of `TRX_PRECISION` (1 TRX = 1,000,000 sun) — e.g., 1,999,999 sun — has its `weight` truncated from ~1.999999 down to 1, roughly halving its computed energy entitlement relative to the mathematically correct proportional share. This is a resource-accounting correctness bug: legitimate accounts systematically receive less energy than they are entitled to based on their frozen balance, which can cause their transactions to unexpectedly fail with out-of-energy errors or force them to pay TRX fees they should not owe. The magnitude of loss (up to ~50-100% of the fractional weight component) is directly analogous to the up-to-25%-and-sometimes-zero result described in the referenced report.

### Likelihood Explanation
This code executes unconditionally on the legacy freeze path (whenever the `unfreeze delay` hard fork proposal `supportUnfreezeDelay()` has not been activated on a given network/testnet), and is reachable purely by an unprivileged account freezing TRX for energy via a standard broadcast transaction and subsequently issuing a smart-contract call — no special privileges are required. The defect is present in both the default and the "hardened" (`allowHardenResourceCalculation`) V1 code paths, meaning enabling the hardening feature does not actually correct this specific truncation, only `calculateGlobalLimitV2` (used post unfreeze-delay activation) fixes it.

### Recommendation
Rewrite `calculateGlobalLimitV1` (and the non-hardened branch in `calculateGlobalEnergyLimit`) to multiply `frozeBalance` by `totalLimit` before dividing by `TRX_PRECISION * totalWeight`, matching the approach already implemented in `calculateGlobalLimitV2`:
```java
return BigInteger.valueOf(frozeBalance)
    .multiply(BigInteger.valueOf(totalLimit))
    .divide(BigInteger.valueOf(TRX_PRECISION).multiply(BigInteger.valueOf(totalWeight)))
    .longValueExact();
```
This ensures fractional TRX below `TRX_PRECISION` is preserved through the multiplication and only truncated once, at the very end.

### Proof of Concept
Using the existing formula with `frozeBalance = 1_999_999`, `TRX_PRECISION = 1_000_000`, `totalLimit = 50_000_000_000`, `totalWeight = 1_234_567`:
```
// Current (buggy) V1 implementation:
weight = 1_999_999 / 1_000_000 = 1          // truncated!
result = 1 * (50_000_000_000 / 1_234_567) ≈ 40_500

// Correct (multiply-before-divide) implementation:
result = (1_999_999 * 50_000_000_000) / (1_000_000 * 1_234_567) ≈ 80_999
```
The current implementation returns roughly half the energy limit the account is proportionally entitled to, matching the same class of error demonstrated in the external report's `testPrecisionLoss` PoC.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L102-119)
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
```

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L161-165)
```java
    if (hardenCalculation()) {
      return calculateGlobalLimitV1(frozeBalance, totalEnergyLimit, totalEnergyWeight);
    }
    long energyWeight = frozeBalance / TRX_PRECISION;
    return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
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
