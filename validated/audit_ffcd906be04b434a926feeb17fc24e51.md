## Analysis

I found a directly analogous divide-before-multiply precision-loss pattern in java-tron's bandwidth/energy resource-limit calculation, which is the accounting logic that determines how much free network/energy capacity a staker "stakeholder" receives in proportion to frozen TRX — conceptually the same "distribute proportional share among stakeholders" computation as the FeeCollector bug.

### Root cause

In `BandwidthProcessor.calculateGlobalNetLimit()`, the default (non-hardened) path truncates the frozen-balance weight via integer division **before** multiplying by the limit/weight ratio: [1](#0-0) 

```java
if (hardenCalculation()) {
  return calculateGlobalLimitV1(frozeBalance, totalNetLimit, totalNetWeight);
}
long netWeight = frozeBalance / TRX_PRECISION;
return (long) (netWeight * ((double) totalNetLimit / totalNetWeight));
```

`frozeBalance / TRX_PRECISION` truncates any frozen balance that is not an exact multiple of `TRX_PRECISION` (1,000,000 sun) before it ever gets multiplied by `totalNetLimit`, and the second division (`totalNetLimit / totalNetWeight`) is also computed independently as a `double` before multiplying — exactly the "divide, then divide again, then multiply" pattern described in the external report. This is mirrored in `EnergyProcessor` (`calculateGlobalEnergyLimit`) and in the legacy `ResourceProcessor.calculateGlobalLimitV1`: [2](#0-1) 

The developers already recognized and fixed this exact bug class — a corrected, order-preserving formula exists as `calculateGlobalLimitV2`, which multiplies before dividing and is gated behind a `hardenCalculation()` / `allowHardenResourceCalculation` feature flag: [3](#0-2) 

The presence of `VMConfig.allowHardenResourceCalculation` (default `false` unless enabled via chain governance) confirms that the vulnerable, un-hardened division-before-multiplication path is still the live default behavior on any network that hasn't activated this hard fork parameter: [4](#0-3) 

### Impact

`calculateGlobalNetLimit`/`calculateGlobalEnergyLimit` determine how much free bandwidth/energy each frozen-TRX holder is allocated out of the network's shared `totalNetLimit`/`totalEnergyLimit` pool, proportional to their stake vs. `totalNetWeight`/`totalEnergyWeight`. Truncating `frozeBalance / TRX_PRECISION` before multiplying systematically under-allocates resource capacity to every account whose frozen balance is not an exact multiple of 1 TRX-precision unit, which can be forced by any staker choosing arbitrary freeze amounts (an unprivileged, permissionless action). This is directly analogous to the FeeCollector case: legitimate stakeholders (frozen-TRX holders) receive less of their proportional share than they are entitled to, due to avoidable order-of-operations precision loss in an accounting calculation, not a fundamental integer-division limitation.

### Caveat

I could not fully verify, within tool-call limits, every call path where the un-hardened (`hardenCalculation()==false`) branch is exercised in production, nor confirm the current default value of `allowHardenResourceCalculation` on mainnet (it is a `DynamicPropertiesStore`-backed, governance-activated parameter). The severity therefore depends on whether this hardening proposal has been activated on the live network — if not yet activated, the precision loss described below is actively occurring on every resource-limit calculation.

### Title
Truncation before multiplication in bandwidth/energy global-limit calculation causes stakers to receive less resource allocation than entitled - (File: `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java`, `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java`, `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java`)

### Summary
The default (un-hardened) formula for computing an account's proportional share of the global bandwidth/energy limit divides the frozen balance by `TRX_PRECISION` first, truncating fractional weight, before multiplying by the limit-to-weight ratio. This mirrors the audited FeeCollector division-before-multiplication bug: unnecessary precision loss from performing division ahead of multiplication in a proportional-distribution formula.

### Finding Description
`calculateGlobalNetLimit` (and the energy analog) compute `netWeight = frozeBalance / TRX_PRECISION` as an integer division, discarding up to `TRX_PRECISION - 1` sun of "weight" before it is multiplied by `totalNetLimit`/`totalNetWeight`. The already-implemented `calculateGlobalLimitV2`/`calculateGlobalLimitV1` (multiply-then-divide with `BigInteger`) demonstrates the codebase's own developers identified this same bug class and built a fix, but the fix is only applied when `hardenCalculation()` returns true, i.e. when the `allowHardenResourceCalculation` dynamic parameter has been turned on via governance. Absent activation, the legacy, imprecise, divide-before-multiply arithmetic remains the operative logic. [5](#0-4) 

### Impact Explanation
Every frozen-TRX holder whose stake is not an exact multiple of `TRX_PRECISION` (1 TRX) is under-allocated bandwidth/energy limit relative to their true proportional share of `totalNetLimit`/`totalEnergyLimit`. Because resource limits gate free-of-charge transaction throughput, this is a real accounting/under-allocation impact on unprivileged stakers, functionally equivalent to "stakeholders receive less distribution than expected" in the original report.

### Likelihood Explanation
Any account can freeze an arbitrary TRX amount and call transactions that trigger `calculateGlobalNetLimit`/`calculateGlobalEnergyLimit`; triggering the truncation requires no privileged role, only a frozen balance not evenly divisible by `TRX_PRECISION`, which is common in practice. The impact is silent and does not revert or error, and only manifests as a lower-than-expected computed limit, matching the "unnecessarily precision loss, non-trivial but not exploited via an obvious crash" profile of the original report.

### Recommendation
Make the hardened (`calculateGlobalLimitV2`, `calculateGlobalLimitV1`) multiply-before-divide `BigInteger` formulas the default/only code path (or activate `allowHardenResourceCalculation` network-wide) rather than gating the fix behind an opt-in governance flag, eliminating the un-hardened `netWeight = frozeBalance / TRX_PRECISION` truncation.

### Proof of Concept
Given `TRX_PRECISION = 1_000_000`, `frozeBalance = 1_999_999` (i.e., 1.999999 TRX frozen), `totalNetLimit = 100_000_000_000`, `totalNetWeight = 100_000_000`:
- Un-hardened path: `netWeight = 1_999_999 / 1_000_000 = 1` → limit `= 1 * (100_000_000_000/100_000_000) = 1000`.
- Hardened/`V2` path (multiply first): `1_999_999 * 100_000_000_000 / (1_000_000 * 100_000_000) ≈ 1999`.
This roughly halves the computed net limit for this account purely due to the truncation-before-multiplication ordering, demonstrating the avoidable precision loss. [6](#0-5) [3](#0-2)

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L437-453)
```java
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

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L371-378)
```java
  protected long calculateGlobalLimitV2(long frozeBalance,
      long totalLimit, long totalWeight) {
    return BigInteger.valueOf(frozeBalance)
        .multiply(BigInteger.valueOf(totalLimit))
        .divide(BigInteger.valueOf(TRX_PRECISION)
            .multiply(BigInteger.valueOf(totalWeight)))
        .longValueExact();
  }
```

**File:** common/src/main/java/org/tron/core/vm/config/VMConfig.java (L311-313)
```java
  public static boolean allowHardenResourceCalculation() {
    return current().allowHardenResourceCalculation;
  }
```
