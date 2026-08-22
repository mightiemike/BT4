### Title
Missing zero-validation of `totalEnergyWeight` chain-parameter causes unlimited/undefined energy limit - ([File: chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java])

### Summary
`EnergyProcessor.calculateGlobalEnergyLimit()` reads `totalEnergyWeight` from `DynamicPropertiesStore` — a globally-shared, network-wide accounting value analogous to an oracle-fed input consumed elsewhere in the system — and uses it as a divisor without properly validating that it is non-zero/non-stale before use, unlike the equivalent bandwidth code path.

### Finding Description
`EnergyProcessor.calculateGlobalEnergyLimit()` guards the `totalEnergyWeight` value only with an `assert`, which is a no-op unless the JVM is started with `-ea` (not the default in production): [1](#0-0) 

```
long totalEnergyLimit = dynamicPropertiesStore.getTotalEnergyCurrentLimit();
long totalEnergyWeight = dynamicPropertiesStore.getTotalEnergyWeight();
if (dynamicPropertiesStore.allowNewReward() && totalEnergyWeight <= 0) {
  return 0;
} else {
  assert totalEnergyWeight > 0;
}
...
long energyWeight = frozeBalance / TRX_PRECISION;
return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
```

Compare this to the parallel, analogous bandwidth-limit computation in the same class hierarchy, which contains an *additional*, unconditional zero-check that `EnergyProcessor` lacks: [2](#0-1) 

```
long totalNetLimit = chainBaseManager.getDynamicPropertiesStore().getTotalNetLimit();
long totalNetWeight = chainBaseManager.getDynamicPropertiesStore().getTotalNetWeight();
if (dynamicPropertiesStore.allowNewReward() && totalNetWeight <= 0) {
  return 0;
}
if (totalNetWeight == 0) {
  return 0;
}
```

`BandwidthProcessor` treats `totalNetWeight == 0` as invalid input regardless of the `allowNewReward()` feature flag and safely returns `0`. `EnergyProcessor`'s equivalent code path only checks for the zero condition when `allowNewReward()` is enabled; otherwise it silently proceeds to divide by the unchecked value with only a disabled `assert` as a guard — mirroring the reported bug class of "trusting an externally/globally sourced numeric input without validating it isn't zero/stale before using it in downstream math."

If `totalEnergyWeight` is `0` (e.g., on a private/consortium chain at genesis before any account has frozen TRX for energy, or if it becomes zero through withdrawal accounting) and `allowNewReward()` is not enabled, `energyWeight * ((double) totalEnergyLimit / totalEnergyWeight)` evaluates to `Infinity` in floating point (since `totalEnergyLimit > 0`), and the subsequent cast `(long) Infinity` yields `Long.MAX_VALUE`. `calculateGlobalEnergyLimit()` is the function that bounds how much energy an account with frozen TRX may consume for TVM execution: [3](#0-2) 

It is exposed directly via `Wallet.getAccountResource` (an RPC-reachable API) and is the value used to gate energy consumption for smart-contract execution.

### Impact Explanation
If reached, an account with any non-trivial frozen-for-energy balance obtains an effectively unbounded energy limit (`Long.MAX_VALUE`), which would let it execute unlimited/very large smart-contract computation without properly-bounded resource accounting, undermining the energy economic model that all TVM execution and network resource accounting relies on. This is a resource-accounting corruption / potential DoS vector on any node/chain configuration where `totalEnergyWeight` can reach `0` while `allowNewReward()` is not active (private/test/consortium TRON networks, or any codepath that transiently zeroes this dynamic property).

### Likelihood Explanation
On established, long-running public mainnet where large amounts of TRX are perpetually frozen for energy, `totalEnergyWeight` reaching exactly `0` is unlikely under normal operating conditions. However, this is squarely reachable on private/test/consortium java-tron deployments immediately after genesis (before the first energy freeze) or in any scenario where the counter can be driven to zero, and the missing check (present in the parallel bandwidth code but absent here) indicates an inconsistency/gap in input validation rather than a deliberately-accepted design choice.

### Recommendation
Add the same unconditional `totalEnergyWeight == 0` (and `totalEnergyLimit <= 0`) guard to `EnergyProcessor.calculateGlobalEnergyLimit()` (and to `RepositoryImpl.calculateGlobalEnergyLimit()`, which contains the identical `assert totalEnergyWeight > 0` pattern) that already exists in `BandwidthProcessor.calculateGlobalNetLimit()`, returning `0` (or otherwise safely handling the value) instead of relying on an `assert` statement that is disabled by default in production JVMs.

### Proof of Concept
Not independently executed; the flaw is demonstrated by direct code comparison:
1. Set `totalEnergyWeight` to `0` in `DynamicPropertiesStore` while `allowNewReward()` returns `false` and `hardenCalculation()`/`allowHardenResourceCalculation()` returns `false` (default state on a freshly-initialized chain/config).
2. Call `EnergyProcessor.calculateGlobalEnergyLimit()` for any account with `frozeBalance >= TRX_PRECISION`.
3. The `assert totalEnergyWeight > 0;` statement is compiled out / disabled by default, execution falls through to `(long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight))`, which evaluates to `Long.MAX_VALUE` due to floating-point division by zero, instead of the safe `0` returned by the analogous `BandwidthProcessor.calculateGlobalNetLimit()` under the same zero-weight condition (lines cited above).

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L440-452)
```java
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
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1672-1677)
```java
    long energyLimit = energyProcessor
        .calculateGlobalEnergyLimit(accountCapsule);
    long totalEnergyLimit =
        chainBaseManager.getDynamicPropertiesStore().getTotalEnergyCurrentLimit();
    long totalEnergyWeight =
        chainBaseManager.getDynamicPropertiesStore().getTotalEnergyWeight();
```
