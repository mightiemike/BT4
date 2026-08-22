### Title
Division by Zero (ArithmeticException) in `EnergyProcessor.calculateGlobalEnergyLimit` via ineffective `assert` guard - (File: `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java`)

### Summary
`EnergyProcessor.calculateGlobalEnergyLimit` relies on a Java `assert` statement to guard against a zero `totalEnergyWeight`, but Java assertions are disabled by default at runtime (no `-ea` flag), making the guard a no-op. When the "harden" calculation path is active, the code performs a real `BigInteger` division by `totalEnergyWeight`, which throws an uncaught `ArithmeticException` if that value is zero. This function is invoked from `EnergyProcessor.useEnergy`, which is on the hot path for every TVM contract call that consumes energy, so it is reachable from ordinary broadcast transactions.

### Finding Description [1](#0-0) 

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

When `supportUnfreezeDelay()` is false (legacy path) and `allowNewReward()` is false (or `totalEnergyWeight` is exactly 0 without `allowNewReward()` guarding it), the only protection against a zero `totalEnergyWeight` is `assert totalEnergyWeight > 0;`. Java assertions are disabled by default in production JVMs unless started with `-ea`, so this statement executes as a no-op and provides no actual protection.

If `hardenCalculation()` (governed by the `ALLOW_HARDEN_RESOURCE_CALCULATION` chain parameter) is enabled and `totalEnergyWeight` is 0, execution falls into `calculateGlobalLimitV1`: [2](#0-1) 

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

`BigInteger.divide(BigInteger.ZERO)` throws `ArithmeticException: BigInteger divide by zero`. This is the exact division-by-zero bug class described in the external report (an unguarded/ineffectively-guarded denominator that can become zero), reachable through a code path that executes for every energy-consuming transaction.

This is directly reachable from `EnergyProcessor.useEnergy`, which runs whenever any account's energy usage is checked/consumed during TVM execution: [3](#0-2) 

Notably, the sibling method for bandwidth, `BandwidthProcessor.calculateGlobalNetLimit`, does contain an explicit `if (totalNetWeight == 0) { return 0; }` check before the same style of division: [4](#0-3) 

The equivalent explicit check is missing for the energy path in this legacy branch, exposing the asymmetry/bug.

The same unguarded pattern is duplicated in `RepositoryImpl.calculateGlobalEnergyLimit` (used by the TVM `Repository`/native-contract layer): [5](#0-4) 

### Impact Explanation
An `ArithmeticException` thrown during energy accounting occurs inside deterministic transaction/block execution code executed by every full node validating the block. An uncaught exception here can crash or halt block processing on nodes reaching this code path, resulting in a denial-of-service condition, and if only some nodes hit the exception (e.g., due to differing configuration state or timing of `totalEnergyWeight` becoming zero), it could also lead to consensus divergence between nodes that do/don't crash.

### Likelihood Explanation
`totalEnergyWeight` is a dynamic, chain-wide aggregate of all accounts' frozen-for-energy weight. It legitimately trends toward zero at genesis, on a freshly bootstrapped/private chain before any account freezes balance for energy, or in edge cases where all such freezes are withdrawn. This path is only reachable in the "legacy" resource model (`supportUnfreezeDelay()` false) and when the `ALLOW_HARDEN_RESOURCE_CALCULATION` proposal is enabled, so on current mainnet where `supportUnfreezeDelay` is already active this specific branch is not hit; however, the same ineffective `assert`-only guard pattern remains latent in the code and is directly exercisable on any network (private chain, testnet, or future configuration) where the legacy path is active, making it a real and reachable code defect rather than a purely theoretical one.

### Recommendation
Replace the `assert totalEnergyWeight > 0;` no-op guard with an explicit runtime check (mirroring `BandwidthProcessor.calculateGlobalNetLimit`'s `if (totalNetWeight == 0) { return 0; }`) before performing any division, in both `EnergyProcessor.calculateGlobalEnergyLimit` and `RepositoryImpl.calculateGlobalEnergyLimit`. Do not rely on Java `assert` statements for any production safety-critical validation, since they are disabled by default.

### Proof of Concept
1. Deploy/operate a chain (or private/test network) where `supportUnfreezeDelay()` is false and the `ALLOW_HARDEN_RESOURCE_CALCULATION` proposal is enabled via `ProposalUtil`/`ProposalService`.
2. Drive `totalEnergyWeight` (`DynamicPropertiesStore.getTotalEnergyWeight()`) to `0` (e.g., at genesis before any `FreezeBalanceActuator`/`FreezeBalanceV2Actuator` freezes for energy, or after all such freezes are withdrawn via `UnfreezeBalanceActuator`/`UnfreezeBalanceV2Actuator`).
3. Submit any transaction that triggers `EnergyProcessor.useEnergy` (e.g., a smart contract call) for an account with `frozeBalance >= TRX_PRECISION`.
4. `calculateGlobalEnergyLimit` proceeds past the no-op `assert`, calls `calculateGlobalLimitV1`, which executes `BigInteger.valueOf(totalEnergyLimit).divide(BigInteger.valueOf(0))`, throwing `ArithmeticException: BigInteger divide by zero` during transaction/block processing.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L102-106)
```java
  public boolean useEnergy(AccountCapsule accountCapsule, long energy, long now) {

    long energyUsage = accountCapsule.getEnergyUsage();
    long latestConsumeTime = accountCapsule.getAccountResource().getLatestConsumeTimeForEnergy();
    long energyLimit = calculateGlobalEnergyLimit(accountCapsule);
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

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L440-453)
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
