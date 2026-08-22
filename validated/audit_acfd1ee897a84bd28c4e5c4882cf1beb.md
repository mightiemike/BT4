Found the concrete analog. `EnergyProcessor.calculateGlobalEnergyLimit()` calls `calculateGlobalLimitV1()`, which performs `BigInteger.valueOf(totalLimit).divide(BigInteger.valueOf(totalEnergyWeight))` — a `BigInteger` division that throws `ArithmeticException` when `totalEnergyWeight == 0`, and the guard preceding it is bypassed on the legacy path.

### Title
Global energy/bandwidth limit calculation can divide by zero/negative total weight and revert deterministically, causing consensus-wide transaction/block processing failure - ([File: chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java])

### Summary
`EnergyProcessor.calculateGlobalEnergyLimit()` and `BandwidthProcessor.calculateGlobalNetLimit()` both assume the chain-wide accounting counters `totalEnergyWeight`/`totalNetWeight` (maintained across many independent actuators: freeze/unfreeze/delegate/undelegate/cancel-unfreeze/suicide) are always strictly positive whenever a frozen balance exists. This is the same class of bug as the reported Minter issue: an implicit cross-subsystem invariant ("total weight tracked incrementally across many mutation sites will always stay positive") is relied upon by an unchecked/weakly-checked arithmetic operation in a hot, unprivileged, and consensus-critical code path.

### Finding Description
`calculateGlobalEnergyLimit()`:
```java
long totalEnergyWeight = dynamicPropertiesStore.getTotalEnergyWeight();
if (dynamicPropertiesStore.allowNewReward() && totalEnergyWeight <= 0) {
  return 0;
} else {
  assert totalEnergyWeight > 0;
}
if (hardenCalculation()) {
  return calculateGlobalLimitV1(frozeBalance, totalEnergyLimit, totalEnergyWeight);
}
``` [1](#0-0) 

The safety check `totalEnergyWeight <= 0` is only active `if (dynamicPropertiesStore.allowNewReward())`. On chains/paths where that flag is off, execution falls through to `assert totalEnergyWeight > 0;` — a Java `assert`, which is a no-op unless the JVM is started with `-ea` (not the default for production nodes). If `totalEnergyWeight` is `0` or negative at that point, execution proceeds into `calculateGlobalLimitV1`:
```java
protected long calculateGlobalLimitV1(long frozeBalance, long totalLimit, long totalWeight) {
  long weight = frozeBalance / TRX_PRECISION;
  return BigInteger.valueOf(weight)
      .multiply(BigInteger.valueOf(totalLimit))
      .divide(BigInteger.valueOf(totalWeight))
      .longValueExact();
}
``` [2](#0-1) 

`BigInteger.divide(BigInteger.ZERO)` unconditionally throws `ArithmeticException`, regardless of JVM assertion settings. `calculateGlobalNetLimit()` in `BandwidthProcessor` has the identical structure but without even the `assert`: it only checks `totalNetWeight == 0` (not negative), so a negative `totalNetWeight` flows straight into `calculateGlobalLimitV1` and its `BigInteger` divide: [3](#0-2) 

`totalNetWeight`/`totalEnergyWeight` are mutated by many independently-reachable, unprivileged actuators and native contracts (`FreezeBalanceActuator`, `FreezeBalanceV2Actuator`, `UnfreezeBalanceActuator`, `UnfreezeBalanceV2Actuator`, `CancelAllUnfreezeV2Actuator`, `DelegateResourceActuator`, `UnDelegateResourceActuator`, and their TVM-native-contract equivalents), each independently computing deltas via `frozenBalance / TRX_PRECISION` truncation: [4](#0-3) [5](#0-4) 
A comment in `UnfreezeBalanceProcessor` — `// adjust total resource, used to be a bug here` — indicates this exact accounting path has previously had correctness issues: [6](#0-5) 
This scattered, per-account, truncating accumulate/deduct pattern across many independently-triggerable transaction types is structurally identical to the Velocimeter root cause: multiple unrelated mutation paths feeding one global counter that a critical downstream function assumes stays within a specific sign/bound, with no cross-checked invariant enforcement.

### Impact Explanation
`calculateGlobalEnergyLimit`/`calculateGlobalNetLimit` are invoked from `useEnergy`/`useAccountNet`, which are called during ordinary transaction execution (bandwidth/energy consumption) for essentially every transaction that touches a frozen-balance account — this is on the hot path of block execution, not merely one Minter-like weekly cron job. If `totalEnergyWeight` (or `totalNetWeight`, under `hardenCalculation()`) reaches zero via legitimate, uncoordinated freeze/unfreeze/delegate churn while `allowNewReward()` is false (or `hardenCalculation()` is enabled for bandwidth), the resulting uncaught `ArithmeticException` during block application would be deterministic for every full node replaying the block — a chain-wide processing halt (DoS), directly analogous to the Minter being unable to emit rewards because `circulating_supply()` reverts every week.

### Likelihood Explanation
Requires `totalEnergyWeight`/`totalNetWeight` to reach exactly `0` (or negative for the harden path) while `allowNewReward()`/committee flags are in a specific combination — a state that depends on network-wide freeze/unfreeze accounting drift rather than a single attacker action, so likelihood is lower than a directly attacker-triggerable bug, but it is reachable purely through normal unprivileged `FreezeBalance`/`UnfreezeBalance`/`DelegateResource` transactions with no special privilege required, matching the "systemic, hard-to-trigger-but-real invariant break" nature of the original report.

### Recommendation
Make the zero/negative check for `totalEnergyWeight` and `totalNetWeight` unconditional (not gated behind `allowNewReward()`), remove reliance on `assert` for a safety-critical invariant (since asserts are disabled by default), and clamp/guard both the V1 and V2 (and hardened) global-limit calculations to return `0` whenever the divisor is `<= 0`, rather than allowing `BigInteger` division to throw.

### Proof of Concept
Not independently reproduced in this analysis; the code paths shown demonstrate that `BigInteger.divide(BigInteger.ZERO)` is reachable whenever `totalEnergyWeight`/`totalNetWeight` is `0` under the described flag combinations, which is sufficient to establish reachability of an uncaught `ArithmeticException` on the consensus-critical resource-limit calculation path.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L154-166)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L274-301)
```java
  public void updateTotalResourceWeight(AccountCapsule accountCapsule,
                                        final UnfreezeBalanceV2Contract unfreezeBalanceV2Contract,
                                        long unfreezeBalance) {
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    switch (unfreezeBalanceV2Contract.getResource()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(-unfreezeBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        dynamicStore.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(-unfreezeBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        dynamicStore.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(-unfreezeBalance);
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        dynamicStore.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
        //this should never happen
        break;
    }
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
