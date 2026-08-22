### Title
`EnergyProcessor.calculateGlobalEnergyLimit` / `BandwidthProcessor.calculateGlobalNetLimit` divide by `totalEnergyWeight`/`totalNetWeight`, which can reach 0 under legacy (pre-`allowNewReward`) accounting, causing an `ArithmeticException`-based DoS or an unbounded energy/bandwidth grant - (File: `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java`, `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java`, `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java`)

### Summary
Similar to the Alchemix `totalShares` bug (division by a running aggregate that can be driven to zero by ordinary user actions), java-tron's resource-limit calculation divides account frozen weight by a global aggregate (`totalEnergyWeight` / `totalNetWeight`) that is only guarded against reaching zero when `allowNewReward()` is enabled.

### Finding Description
`EnergyProcessor.calculateGlobalEnergyLimit` and `BandwidthProcessor.calculateGlobalNetLimit` both read a global weight counter and divide by it: [1](#0-0) 

```java
long totalEnergyWeight = dynamicPropertiesStore.getTotalEnergyWeight();
if (dynamicPropertiesStore.allowNewReward() && totalEnergyWeight <= 0) {
  return 0;
} else {
  assert totalEnergyWeight > 0;
}
...
return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
```

The zero-guard is conditioned on `allowNewReward()`. When that flag is off, the code instead relies on `assert totalEnergyWeight > 0;`, which is a no-op in production JVMs (`-ea` is not enabled by default), so no protection actually exists in that mode. The same pattern (guard only under `allowNewReward()`) exists in `BandwidthProcessor.calculateGlobalNetLimit`: [2](#0-1) 

The hardened path (`allowHardenResourceCalculation()`) calls `calculateGlobalLimitV1`, which performs a strict `BigInteger` division by `totalWeight`: [3](#0-2) 

```java
protected long calculateGlobalLimitV1(long frozeBalance, long totalLimit, long totalWeight) {
  long weight = frozeBalance / TRX_PRECISION;
  return BigInteger.valueOf(weight)
      .multiply(BigInteger.valueOf(totalLimit))
      .divide(BigInteger.valueOf(totalWeight))
      .longValueExact();
}
```

If `totalWeight` (i.e. `totalEnergyWeight`/`totalNetWeight`) is `0`, `BigInteger.divide` throws `ArithmeticException: BigInteger divide by zero`. This is the direct analog of the Alchemix `totalShares == 0` division-by-zero: the aggregate `totalEnergyWeight`/`totalNetWeight` is incremented on freeze and decremented on unfreeze (`FreezeBalanceActuator`, `UnfreezeBalanceActuator`, `FreezeBalanceV2Actuator`/`UnfreezeBalanceV2Actuator`, and their TVM native-contract counterparts `FreezeBalanceV2Processor`/`UnDelegateResourceProcessor`), and there is no mechanism preventing it from reaching exactly zero, e.g. if every account that ever froze balance for ENERGY/BANDWIDTH fully unfreezes: [4](#0-3) [5](#0-4) 

Note `addTotalEnergyWeight`/`addTotalNetWeight` only clamp the value to `max(0, ...)` when `allowNewReward()` is true: [6](#0-5) 

Once the aggregate is exactly `0` under legacy (`allowNewReward()` disabled) or hardened-calculation configurations, every call path that computes an account's resource limit (`useEnergy`, `useAccountNet`, `calculateGlobalEnergyLimit`, `Wallet.calcCanDelegatedEnergyMaxSize`, `getAccountResource` RPC) becomes reachable from ordinary broadcast transactions (freeze/unfreeze/transfer/any transaction consuming energy or bandwidth) and TVM contract calls that consume energy, and would throw an uncaught `ArithmeticException` in the hardened path, or silently compute `Infinity`/`NaN` truncated to an extreme `long` value (e.g. `Long.MAX_VALUE`) in the legacy double-arithmetic path — granting effectively unlimited free energy/bandwidth.

### Impact Explanation
- In the hardened-calculation configuration, an uncaught `ArithmeticException` inside transaction processing (`useEnergy`/`useAccountNet`, invoked for virtually every transaction and TVM execution) would abort block/transaction processing, a consensus-affecting DoS.
- In the legacy double-arithmetic path, dividing by zero doesn't throw but yields `Infinity`, which truncates to `Long.MAX_VALUE` when cast to `long`, effectively granting an account (or all accounts) unlimited free energy/bandwidth — a resource-accounting corruption that could be used to spam the network without paying resource costs.
- Both classes correspond directly to the reported bug class: a shared aggregate denominator that legitimate user actions (freeze/unfreeze) can drive to zero, with insufficient protection outside one specific feature flag.

### Likelihood Explanation
Requires either (a) `allowNewReward()` disabled (a legacy/pre-upgrade configuration) combined with the total frozen weight for a resource type reaching exactly zero (all accounts having unfrozen their stake for that resource), or (b) `allowHardenResourceCalculation()` enabled while the total weight reaches zero regardless of `allowNewReward()`, since the guard in `calculateGlobalEnergyLimit`/`calculateGlobalNetLimit` is bypassed once the hardened branch is entered — actually note the same `if (dynamicPropertiesStore.allowNewReward() && totalEnergyWeight <= 0) return 0;` guard runs before the hardened branch is chosen, so the hardened branch is also short-circuited when `allowNewReward()` is true. The exposure is therefore concentrated in networks/configurations where `allowNewReward()` is off. On current mainnet, `allowNewReward()` is enabled, reducing real-world likelihood, but the code path remains latent and reachable on any private/test network or future state where the flag toggles differently, and the `assert` is not a real safety net in production.

### Recommendation
Remove reliance on `assert` for correctness; explicitly guard `totalEnergyWeight`/`totalNetWeight` against `<= 0` unconditionally (not only when `allowNewReward()` is true) in both `EnergyProcessor.calculateGlobalEnergyLimit`/`calculateGlobalEnergyLimitV2` and `BandwidthProcessor.calculateGlobalNetLimit`/`calculateGlobalNetLimitV2`, returning `0` (or another safe default) before any division. Additionally, ensure `addTotalEnergyWeight`/`addTotalNetWeight`/`addTotalTronPowerWeight` clamp to a non-negative floor unconditionally, not only under `allowNewReward()`.

### Proof of Concept
1. Deploy/operate a network configuration with `allowNewReward()` disabled (or simply reach a state where `totalEnergyWeight` becomes `0` while `allowHardenResourceCalculation()` is enabled, bypassing the `allowNewReward()` short-circuit only being conditioned as shown).
2. Have every account holding frozen ENERGY balance broadcast `UnfreezeBalanceContract`/`UnfreezeBalanceV2Contract` (or via `UnDelegateResourceProcessor` TVM path) until `dynamicPropertiesStore.getTotalEnergyWeight()` reaches exactly `0`.
3. Broadcast any transaction that consumes energy (e.g., a smart contract call) or query `getAccountResource` via gRPC — this invokes `EnergyProcessor.calculateGlobalEnergyLimit`, which either throws `BigInteger divide by zero` (hardened path) or returns an inflated `long` due to `Infinity` truncation (legacy path), corrupting the node's resource accounting or crashing transaction processing.

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

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L95-121)
```java
        addTotalWeight(BANDWIDTH, dynamicStore, frozenBalance, increment);
        break;
      case ENERGY:
        if (!ArrayUtils.isEmpty(receiverAddress)
            && dynamicStore.supportDR()) {
          increment = delegateResource(ownerAddress, receiverAddress, false,
                  frozenBalance, expireTime);
          accountCapsule.addDelegatedFrozenBalanceForEnergy(frozenBalance);
        } else {
          long oldEnergyWeight = accountCapsule.getEnergyFrozenBalance() / TRX_PRECISION;
          long newFrozenBalanceForEnergy =
              frozenBalance + accountCapsule.getEnergyFrozenBalance();
          accountCapsule.setFrozenForEnergy(newFrozenBalanceForEnergy, expireTime);
          long newEnergyWeight = accountCapsule.getEnergyFrozenBalance() / TRX_PRECISION;
          increment = newEnergyWeight - oldEnergyWeight;
        }
        addTotalWeight(ENERGY, dynamicStore, frozenBalance, increment);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenBalance() / TRX_PRECISION;
        long newFrozenBalanceForTronPower =
            frozenBalance + accountCapsule.getTronPowerFrozenBalance();
        accountCapsule.setFrozenForTronPower(newFrozenBalanceForTronPower, expireTime);
        long newTPWeight = accountCapsule.getTronPowerFrozenBalance() / TRX_PRECISION;
        increment = newTPWeight - oldTPWeight;
        addTotalWeight(TRON_POWER, dynamicStore, frozenBalance, increment);
        break;
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L243-260)
```java
    long weight = dynamicStore.allowNewReward() ? decrease : -unfreezeBalance / TRX_PRECISION;
    switch (unfreezeBalanceContract.getResource()) {
      case BANDWIDTH:
        dynamicStore
            .addTotalNetWeight(weight);
        break;
      case ENERGY:
        dynamicStore
            .addTotalEnergyWeight(weight);
        break;
      case TRON_POWER:
        dynamicStore
            .addTotalTronPowerWeight(weight);
        break;
      default:
        //this should never happen
        break;
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L2282-2306)
```java
  //The unit is trx
  public void addTotalEnergyWeight(long amount) {
    if (amount == 0) {
      return;
    }
    long totalEnergyWeight = getTotalEnergyWeight();
    totalEnergyWeight += amount;
    if (allowNewReward()) {
      totalEnergyWeight = max(0, totalEnergyWeight, disableJavaLangMath());
    }
    saveTotalEnergyWeight(totalEnergyWeight);
  }

  //The unit is trx
  public void addTotalTronPowerWeight(long amount) {
    if (amount == 0) {
      return;
    }
    long totalWeight = getTotalTronPowerWeight();
    totalWeight += amount;
    if (allowNewReward()) {
      totalWeight = max(0, totalWeight, disableJavaLangMath());
    }
    saveTotalTronPowerWeight(totalWeight);
  }
```
