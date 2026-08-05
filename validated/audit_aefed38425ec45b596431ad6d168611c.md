### Title
Missing zero-guard for `totalEnergyWeight` causes uncaught `ArithmeticException` DoS in energy-limit calculation - (File: chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java)

### Summary
This is the same bug class as the reported `SafEth.stake()` issue: a global accounting value that is only ever populated by a single (or a small set of) staker(s) is used as a divisor in a subsequent, unrelated user's calculation, and the code does not defensively guard against that divisor collapsing to zero after the staker withdraws. In `SafEth`, `totalSupply` collapsing to 1 wei plus `underlyingValue` becoming 0 caused `preDepositPrice = 0`, breaking `stake()` for all future users. In java-tron, `totalEnergyWeight` (the sum of all frozen-for-ENERGY balances across the network, decremented on every unfreeze) is used as a divisor in `EnergyProcessor.calculateGlobalEnergyLimit()`, and the current zero-guard is incomplete, unlike the equivalent bandwidth path.

### Finding Description
`EnergyProcessor.calculateGlobalEnergyLimit()` computes an account's energy limit using `totalEnergyLimit / totalEnergyWeight`: [1](#0-0) 

The zero-check is conditional on a feature flag:
```java
if (dynamicPropertiesStore.allowNewReward() && totalEnergyWeight <= 0) {
  return 0;
} else {
  assert totalEnergyWeight > 0;
}
if (hardenCalculation()) {
  return calculateGlobalLimitV1(frozeBalance, totalEnergyLimit, totalEnergyWeight);
}
```
If `allowNewReward()` is `false` (a hard-fork/config flag) and `totalEnergyWeight` is `0`, execution falls through the `assert` statement — which is a no-op in production JVMs, since Java assertions are disabled by default unless started with `-ea` — and proceeds to divide by `totalEnergyWeight`. When `hardenCalculation()` (the `allowHardenResourceCalculation` flag) is enabled, this reaches: [2](#0-1) 

`BigInteger.divide(BigInteger.valueOf(0))` throws an unguarded `ArithmeticException: BigInteger divide by zero`.

By contrast, the bandwidth analog explicitly protects against this exact scenario with a second, unconditional check: [3](#0-2) 
```java
if (dynamicPropertiesStore.allowNewReward() && totalNetWeight <= 0) {
  return 0;
}
if (totalNetWeight == 0) {
  return 0;
}
```
`EnergyProcessor.calculateGlobalEnergyLimit()` has no equivalent unconditional `if (totalEnergyWeight == 0) return 0;` fallback — this is the asymmetry/root cause.

`totalEnergyWeight` is a global, network-wide accumulator that is decremented on every unfreeze (`UnfreezeBalanceActuator`, `UnfreezeBalanceV2Actuator`, `UnfreezeBalanceProcessor`, `CancelAllUnfreezeV2Processor`) exactly analogous to `totalSupply` being decremented on every `unstake()` in the reported vulnerability: [4](#0-3) 

On any network/environment where energy-frozen weight can reach exactly zero (e.g., private/consortium chains, freshly bootstrapped test networks, or transient windows where the sole account(s) holding frozen ENERGY unfreeze), the next call into `EnergyProcessor.useEnergy()`/`calculateGlobalEnergyLimit()` — which is invoked for essentially every TVM contract execution that consumes energy — throws an unhandled `ArithmeticException`.

### Impact Explanation
This is a state/invalid-state and public-work-accounting class of impact analogous to the "sole depositor DoS" report: a single "last unfreezer" (in the same role as SafEth's "sole safETH holder") can drive a global divisor to zero and thereby break a public, unprivileged code path (energy accounting for arbitrary TVM transactions) for all subsequent callers, until an administrator intervenes (e.g., someone re-freezes ENERGY to push `totalEnergyWeight` back above zero, analogous to Asymmetry's remediation of "manually holding safETH"). Because the exception is uncaught, this can manifest as failed/erroring transaction processing for any account attempting to consume ENERGY on the affected chain, which is a concrete availability impact on a public, unprivileged code path, matching the "invalid-state/halt" impact category.

### Likelihood Explanation
Reaching `totalEnergyWeight == 0` on a busy mainnet with `allowNewReward()` already active is unlikely because that flag's own check already returns `0` safely. However, the vulnerable branch is fully reachable whenever `allowNewReward()` is `false` (its historical/pre-hard-fork state, or on any chain/testnet that has not activated it) combined with `allowHardenResourceCalculation` enabled and `totalEnergyWeight` reaching zero — a state that is trivially reachable on private/consortium java-tron deployments or freshly initialized test networks with a small number of energy-freezers, exactly mirroring the "sole holder unstakes" precondition in the original report. This is a genuine code-level defensive gap, not merely theoretical, since the parallel `BandwidthProcessor` code demonstrably required and implements the missing guard.

### Recommendation
Add the same unconditional guard used in `BandwidthProcessor.calculateGlobalNetLimit()` to `EnergyProcessor.calculateGlobalEnergyLimit()`:
```java
if (totalEnergyWeight <= 0) {
  return 0;
}
```
placed before the `hardenCalculation()`/`assert` branch, removing reliance on the `allowNewReward()` flag and on `assert` (which is not enforced in production).

### Proof of Concept
1. Deploy/operate a java-tron network where `allowNewReward` has not been activated (`allowNewReward() == false`) and `allowHardenResourceCalculation` is enabled.
2. Ensure the sole account(s) holding frozen ENERGY balance fully unfreeze (via `UnfreezeBalanceActuator`/`UnfreezeBalanceV2Actuator`), driving `dynamicPropertiesStore.getTotalEnergyWeight()` to exactly `0`.
3. Any subsequent transaction that invokes `EnergyProcessor.useEnergy()` → `calculateGlobalEnergyLimit()` [1](#0-0)  will fall through the no-op `assert` and call `calculateGlobalLimitV1(frozeBalance, totalEnergyLimit, 0)` [2](#0-1) , which throws `ArithmeticException: BigInteger divide by zero`, uncaught in this path.

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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java (L190-201)
```java
    // adjust total resource, used to be a bug here
    switch (param.getResourceType()) {
      case BANDWIDTH:
        repo.addTotalNetWeight(-unfreezeBalance / TRX_PRECISION);
        break;
      case ENERGY:
        repo.addTotalEnergyWeight(-unfreezeBalance / TRX_PRECISION);
        break;
      default:
        //this should never happen
        break;
    }
```
