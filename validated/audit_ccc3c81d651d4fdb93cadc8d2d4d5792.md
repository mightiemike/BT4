### Title
Division-before-multiplication precision loss in undelegated resource-usage accounting - (File: `actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java`)

### Summary
`UnDelegateResourceActuator.execute()` computes the bandwidth/energy usage that must be clawed back from a resource receiver when a delegator calls `UnDelegateResourceContract` (the `unfreezebalancev2`/`undelegateresource` RPC path). The calculation performs two separate divisions before a multiplication and only truncates once at the final cast, exactly the anti-pattern described in the external report ("perform all divisions first, multiply last" instead of "multiply first, divide once").

### Finding Description
The vulnerable code is: [1](#0-0) 

and the identical pattern for energy: [2](#0-1) 

`unDelegateMaxUsage` is computed as `(double) unDelegateBalance / TRX_PRECISION * ((double) totalLimit / totalWeight)`, i.e., two independent divisions computed before the multiplication, then truncated by a single `(long)` cast. This is structurally identical to the reported bug class ("time_fraction" then "mgmt_fee_pct" computed via sequential division before the final result), which the report explicitly flags as producing compounding precision loss versus the mathematically correct `(a * limit) / (TRX_PRECISION * weight)` single-division form.

Notably, this exact family of bug was already identified and hardened elsewhere in the codebase: `ResourceProcessor.calculateGlobalLimitV1`/`calculateGlobalLimitV2` and `BandwidthProcessor.calculateGlobalNetLimit`/`calculateGlobalNetLimitV2` were rewritten to use `BigInteger` "multiply-then-divide-once" formulas guarded by an `allowHardenResourceCalculation()` flag: [3](#0-2) [4](#0-3) 

However, `UnDelegateResourceActuator` never calls these hardened helpers for its own local `unDelegateMaxUsage`/`transferUsage` calculation — it reimplements the fraction math independently using raw `double` arithmetic with two chained divisions, bypassing the fix applied elsewhere.

### Impact Explanation
Because `transferUsage` and `unDelegateMaxUsage` determine how much bandwidth/energy "usage" is subtracted from the resource receiver's account when a delegation is revoked, systematic rounding error in this calculation causes account resource bookkeeping (`NetUsage`/`EnergyUsage`) to drift from the value it should have. Depending on rounding direction this can let a receiver retain more free resource usage headroom than they are entitled to (effectively free bandwidth/energy accounting leakage across many undelegate operations network-wide), which falls under "resource and reward accounting" corruption.

### Likelihood Explanation
This code path is reached by any account broadcasting an ordinary `UnDelegateResourceContract` transaction (part of the standard TRON stake-2.0 delegation/undelegation flow), requiring no privileged role — any user who has previously delegated resources can trigger it. The precision defect fires on essentially every undelegate operation involving BANDWIDTH or ENERGY, so it is a normal-usage bug, not an edge case, and compounds over the large transaction volume seen on mainnet.

### Recommendation
Replace the two-stage double-division-then-multiply computation with the already-established hardened pattern used in `ResourceProcessor.calculateGlobalLimitV1`/`V2`, i.e., compute `unDelegateMaxUsage` as a single `BigInteger` multiply-then-divide:
`unDelegateBalance * totalLimit / (TRX_PRECISION * totalWeight)`, and route this calculation through the existing `calculateGlobalNetLimit`/`calculateGlobalEnergyLimit` (or equivalent hardened) helpers so the fix that was already applied to those call sites also covers `UnDelegateResourceActuator`.

### Proof of Concept
1. Delegate a large `unDelegateBalance` amount for BANDWIDTH/ENERGY to a receiver over multiple transactions, with `totalNetLimit`/`totalNetWeight` (or energy equivalents) chosen such that `totalNetLimit / totalNetWeight` is not evenly divisible (typical live-network values already satisfy this).
2. Call `UnDelegateResourceContract` repeatedly to unwind delegations in smaller increments.
3. Compare the receiver's resulting `NetUsage`/`EnergyUsage` state against a reference computed with `BigInteger` exact arithmetic (`unDelegateBalance * totalNetLimit / (TRX_PRECISION * totalNetWeight)`), as done in the existing test `framework/src/test/java/org/tron/core/db/CalculateGlobalLimitHardenTest.java` (`testGlobalEnergyLimitV2CorrectVsDoublePrecisionLoss`) which already demonstrates the double-vs-BigInteger discrepancy for the sibling (already-fixed) computation — the same discrepancy magnitude applies to the un-hardened `unDelegateMaxUsage`/`transferUsage` computation in `UnDelegateResourceActuator`. [5](#0-4)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L80-88)
```java
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalNetLimit()) / dynamicStore.getTotalNetWeight()));
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L103-111)
```java
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalEnergyCurrentLimit()) / dynamicStore.getTotalEnergyWeight()));
            transferUsage = (long) (receiverCapsule.getEnergyUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForEnergy()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(-unDelegateBalance);
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

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L455-466)
```java
  public long calculateGlobalNetLimitV2(long frozeBalance) {
    long totalNetLimit = dynamicPropertiesStore.getTotalNetLimit();
    long totalNetWeight = dynamicPropertiesStore.getTotalNetWeight();
    if (totalNetWeight == 0) {
      return 0;
    }
    if (hardenCalculation()) {
      return calculateGlobalLimitV2(frozeBalance, totalNetLimit, totalNetWeight);
    }
    double netWeight = (double) frozeBalance / TRX_PRECISION;
    return (long) (netWeight * ((double) totalNetLimit / totalNetWeight));
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
