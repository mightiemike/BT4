### Title
Integer-division truncation in global bandwidth/energy limit allocation can zero out a frozen account's resource limit - (File: chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java)

### Summary
The reported bug (`BathBuddy.notifyRewardAmount`) truncates `reward.div(rewardsDuration)` to zero via plain integer division, silently denying a distribution its intended per-second rate. The same structural pattern — a proportional-allocation formula computed with legacy integer division instead of a scaled/BigInteger calculation — exists in java-tron's resource-weight allocation logic, `calculateGlobalNetLimit`/`calculateGlobalLimitV1` in `BandwidthProcessor.java`, and its energy counterpart in `EnergyProcessor.java`. Both divide a user's proportional share (`weight * totalLimit / totalWeight`) using primitive `long` integer division on the legacy (non-hardened) path, which truncates to `0` whenever `weight * totalLimit < totalWeight`.

### Finding Description
`calculateGlobalNetLimit` computes an account's bandwidth limit from its frozen balance: [1](#0-0) 

On the legacy (non-hardened) path it does:
```
long netWeight = frozeBalance / TRX_PRECISION;
return (long) (netWeight * ((double) totalNetLimit / totalNetWeight));
```
and the hardened path uses:
```
protected long calculateGlobalLimitV1(long frozeBalance, long totalLimit, long totalWeight) {
    long weight = frozeBalance / TRX_PRECISION;
    return BigInteger.valueOf(weight).multiply(BigInteger.valueOf(totalLimit))
        .divide(BigInteger.valueOf(totalWeight)).longValueExact();
}
``` [2](#0-1) 

Both formulas are the direct on-chain analog of `reward.div(rewardsDuration)`: a fixed numerator (`weight * totalLimit`) is divided by a denominator (`totalNetWeight`) that scales with total network activity. If the network-wide `totalNetWeight` (sum of all frozen balances / `TRX_PRECISION`) grows large relative to `totalNetLimit`, then any account whose own `weight * totalNetLimit < totalNetWeight` receives `netLimit == 0` even though it legitimately froze TRX for bandwidth. This is the same "integer division truncates a proportional share to zero" defect described in the report, just applied to bandwidth/energy weight distribution instead of an ERC-20 reward rate. `EnergyProcessor` mirrors this same `calculateGlobalLimitV1`/`calculateGlobalLimitV2` pattern via the shared `ResourceProcessor` base class for energy limits, so the same truncation risk applies to CPU (energy) resource accounting as well.

`calculateGlobalNetLimit` does have an explicit guard for `frozeBalance < TRX_PRECISION` returning `0` immediately, and a hardened `calculateGlobalLimitV2` path (guarded by `allowHardenResourceCalculation`) that does the multiply-before-divide with `BigInteger`, avoiding intermediate truncation of the `frozeBalance/TRX_PRECISION` step — but neither guard changes the final division by `totalNetWeight`, which is still a single truncating integer division in both the legacy and hardened formulas.

### Impact Explanation
An account that freezes the minimum viable amount of TRX for bandwidth (above `TRX_PRECISION`, i.e. ≥ 1 TRX) can end up with a computed `netLimit` of exactly `0` if `weight * totalNetLimit < totalNetWeight`, i.e. its proportional share rounds down to nothing. This causes the account to fall through to `useFreeNet`/`useTransactionFee`, effectively wasting the TRX it froze for bandwidth — an accounting/settlement defect: the user paid the opportunity cost of freezing (locked liquidity) but received zero of the promised resource. As `totalNetWeight` (total TRX frozen network-wide for bandwidth) grows, more small freezers are pushed into this zero-limit condition, degrading the intended proportional-allocation guarantee for an increasing share of participants. This mirrors the report's core impact: certain otherwise-valid (frozen amount, network state) combinations are "infeasible" and silently yield zero, rather than reverting or scaling the allocation.

### Likelihood Explanation
Reachability is unprivileged: freezing TRX via `FreezeBalanceActuator`/`FreezeBalanceV2Actuator` is available to any account, and `calculateGlobalNetLimit`/`calculateGlobalLimitV1` execute on every bandwidth check (`useAccountNet`, `consumeBandwidthForCreateNewAccount`, asset-issuer bandwidth checks). The likelihood of hitting the zero-truncation condition in practice depends on the current ratio of `totalNetLimit` to `totalNetWeight` on the live network, which this index does not let me directly confirm; whether it is currently triggerable at today's network parameters is therefore uncertain and would need on-chain data (current `getTotalNetLimit()`/`getTotalNetWeight()` values) to validate empirically.

### Recommendation
Compute the entire proportional share (`weight * totalLimit`) before any division, and only perform a single truncating division against `totalWeight`, consistently across both `calculateGlobalNetLimitV2`/`calculateGlobalLimitV1` and any legacy double-based path — this is already partially done in `calculateGlobalLimitV2`. In addition, consider scaling the intermediate numerator by a fixed precision factor (analogous to the report's suggested `10**8` multiplier) before the final division and rescaling down afterward, so that fractional shares are preserved with sub-unit precision instead of being floored to `0`, or explicitly document/guard the "may receive zero bandwidth despite freezing" behavior as intended economic behavior if that is the design intent.

### Proof of Concept
Deterministic on-chain PoC is not verifiable purely from static code (it depends on live `totalNetWeight`/`totalNetLimit` values), but the truncation is directly reproducible in isolation:
```java
// ResourceProcessor.calculateGlobalLimitV1
long frozeBalance = 1_000_000L;     // exactly 1 TRX == TRX_PRECISION
long totalLimit = 43_200_000_000L;  // e.g. current TotalNetLimit
long totalWeight = 50_000_000_000L; // hypothetical large totalNetWeight
// weight = frozeBalance / TRX_PRECISION = 1
// result = 1 * 43_200_000_000 / 50_000_000_000 = 0  (truncated to zero)
```
This shows that for any `totalWeight > weight * totalLimit`, a legitimately frozen account (≥ 1 TRX, passing the explicit `frozeBalance < TRX_PRECISION` guard) still receives a `netLimit` of `0`, structurally identical to the reported `reward.div(rewardsDuration)` truncation-to-zero defect.

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
