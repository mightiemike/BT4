### Title
Missing zero-guard on `totalEnergyWeight` in `RepositoryImpl.calculateGlobalEnergyLimit` causes division-by-zero DoS during TVM contract execution - (File: actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java)

### Summary
The Revert finding shows that a network-wide accounting variable (`collateralFactor`) can legitimately be driven to `0` through normal admin operations, and one code path that consumes this variable in a core function (`liquidate()`) lacks a guard for the zero case, causing an unconditional revert (division by zero). The analogous pattern exists in java-tron: `totalEnergyWeight`/`totalNetWeight` are global accounting values that several call sites guard against being `0`, but `RepositoryImpl.calculateGlobalEnergyLimit`, used on the TVM contract-execution path, only has a compiled-out `assert` instead of a real guard.

### Finding Description
`RepositoryImpl.calculateGlobalEnergyLimit` divides by `totalEnergyWeight` after only checking it with a Java `assert`, which is disabled by default in production JVMs (no `-ea` flag): [1](#0-0) 

Compare this to the equivalent logic used in `EnergyProcessor.calculateGlobalEnergyLimit`, which explicitly returns `0` if `totalEnergyWeight <= 0`: [2](#0-1) 

And `BandwidthProcessor.calculateGlobalNetLimit`, which has the same explicit `<= 0` and `== 0` guards: [3](#0-2) 

`RepositoryImpl.calculateGlobalEnergyLimit` is reached from `VMActuator.getTotalEnergyLimitWithFixRatio`, which is executed on every `TriggerSmartContract` transaction where the caller is not the contract creator, in order to compute how much of the creator's frozen energy should subsidize the call: [4](#0-3) 

If `totalEnergyWeight` is ever `0` — e.g. because all energy-frozen balances network-wide have been unfrozen (a state reachable through the normal, unprivileged `UnfreezeBalanceActuator`/`UnfreezeBalanceV2Actuator` flow, no admin permission required) — the `hardenResourceCalculation()` path performs `BigInteger.divide(BigInteger.ZERO)`, which throws `ArithmeticException`. The non-hardened path divides a `double` by `0`, producing `Infinity`/`NaN`, which then gets truncated to `long` via a cast, yielding an incorrect (garbage) energy limit rather than a clean failure. Neither outcome is handled gracefully by the caller, unlike the sibling implementations that pre-empt the divide with a guard.

### Impact Explanation
This mirrors the core issue in the Revert report: a legitimate, normal state transition (unfreezing) of a shared accounting denominator, without any additional validation, breaks a downstream core computation used on every relevant transaction (contract invocation energy accounting), either by throwing an uncaught runtime exception (denial of service for `TriggerSmartContract` processing when the creator does not itself call the contract) or by silently computing an incorrect creator energy subsidy (accounting corruption: too much/too little energy is charged from the creator vs. the caller). Because `calculateGlobalEnergyLimit` in `RepositoryImpl` lacks the same defensive check present in `EnergyProcessor`/`BandwidthProcessor`, this is a genuine inconsistency/regression risk rather than a deliberate design decision.

### Likelihood Explanation
Reaching `totalEnergyWeight == 0` network-wide is unlikely on a live, active mainnet with broad participation, since it requires the sum of all `FrozenForEnergy`/frozen-for-energy-v2 balances to be zero. It is far more plausible on: a fresh private/test network, right after a chain-wide unfreeze event, or in any deployment with limited freeze participation — all reachable purely through normal, unprivileged `FreezeBalance`/`UnfreezeBalance` actuators, no privileged actor or leaked key required. The trigger condition itself (any account calling another contract's `TriggerSmartContract` where creator != caller) is a completely standard, anonymous, broadcast-transaction code path.

### Recommendation
Add the same `totalEnergyWeight <= 0` (and `== 0`) short-circuit guard to `RepositoryImpl.calculateGlobalEnergyLimit` that already exists in `EnergyProcessor.calculateGlobalEnergyLimit` and `BandwidthProcessor.calculateGlobalNetLimit`, returning `0` instead of relying on a disabled `assert`, so behavior is consistent across all resource-limit calculation call sites and never throws or silently miscomputes on the zero-weight edge case.

### Proof of Concept
1. On a test network, drive `DynamicPropertiesStore.getTotalEnergyWeight()` to `0` by having all accounts with `FrozenForEnergy`/`FrozenV2` energy balances fully unfreeze (via `UnfreezeBalanceActuator`/`UnfreezeBalanceV2Actuator`), while a contract creator retains `allFrozenBalanceForEnergy >= TRX_PRECISION`.
2. Have a different account (not the contract creator) call `TriggerSmartContract` on that creator's contract.
3. `VMActuator.getTotalEnergyLimitWithFixRatio` invokes `rootRepository.getAccountLeftEnergyFromFreeze`/`calculateGlobalEnergyLimit` path down to `RepositoryImpl.calculateGlobalEnergyLimit`, which divides by the now-zero `totalEnergyWeight`.
4. With `allowHardenResourceCalculation` enabled, `BigInteger.divide(BigInteger.ZERO)` throws `ArithmeticException`, propagating up through `getTotalEnergyLimitWithFixRatio` and aborting normal transaction processing for that call; with hardening disabled, the double-division-by-zero silently yields an incorrect creator energy limit.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L742-756)
```java
    if (consumeUserResourcePercent <= 0) {
      creatorEnergyLimit = min(originEnergyLeft, originEnergyLimit,
          VMConfig.disableJavaLangMath());
    } else {
      if (consumeUserResourcePercent < VMConstant.ONE_HUNDRED) {
        // creatorEnergyLimit =
        // min(callerEnergyLimit * (100 - percent) / percent,
        //   creatorLeftFrozenEnergy, originEnergyLimit)

        creatorEnergyLimit = min(
            BigInteger.valueOf(callerEnergyLimit)
                .multiply(BigInteger.valueOf(VMConstant.ONE_HUNDRED - consumeUserResourcePercent))
                .divide(BigInteger.valueOf(consumeUserResourcePercent)).longValueExact(),
            min(originEnergyLeft, originEnergyLimit, VMConfig.disableJavaLangMath()),
            VMConfig.disableJavaLangMath());
```
